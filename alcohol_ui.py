"""Read-only, mobile friendly catalog; upstream crawling is never called here."""
import html
import json
import os
from pathlib import Path
import alcohol as a
import reliability as r
from persistence import sync_public_snapshot


def render(st,is_admin=False):
    st.subheader('アルコール情報')
    st.caption('東京ディズニーリゾートの非公式情報サイトです。20歳未満の飲酒は禁止されています。')
    st.caption('公式に掲載された販売情報を整理しています。当日の在庫・営業・提供を保証しません。')
    directory=Path(os.environ.get('DISNEY_DB_DIR','.runtime'))
    conn=None
    try:
        conn=a.connect(directory/'alcohol.db')
        a.bootstrap(conn)
        sync=sync_public_snapshot(conn,'alcohol.db',directory)
        freshness=a.freshness(conn);rows=a.catalog(conn)
        selected=st.segmented_control('アルコール情報のパーク',['ランド','シー','両方'],default='両方',key='alcohol_park') or '両方'
        parks={'ランド':['Disneyland'],'シー':['DisneySea'],'両方':[]}[selected]
        chosen=[x for x in rows if not parks or x['park'] in parks]
        local=lambda value:r.utc(value).tz_convert(r.JST).strftime('%Y/%m/%d %H:%M JST') if value else '未確認'
        st.write(f"最終成功: {local(freshness['last_success_utc'])}")
        st.caption(f"次回更新予定: {local(freshness['next_scheduled_jst'])}（毎週月曜6時・起動の遅れがあり得ます）")
        if freshness['level']=='warning': st.error('14日以上、更新に成功していません。公式ページで最新情報を確認してください。')
        elif freshness['level']=='attention': st.warning('8日以上、更新に成功していません。')
        elif freshness['level']=='unavailable': st.warning('確認済みの商品はまだ登録されていません。')
        else: st.caption(f"情報の鮮度: {freshness['age_days']:.1f}日前の確認")
        latest=freshness['latest_run']
        if latest and latest['status']=='failed': st.warning('直近の更新は失敗しました。前回の確認データを表示しています。')
        if latest and latest['status']=='partial': st.warning('直近の更新は一部未確認です。失敗した店舗は前回データを保持しています。')
        st.write(f"掲載商品数: {len({x['product_id'] for x in chosen})}件（将来・期間終了を含む）")
        with st.expander('調査範囲と確認状況'):
            good=a.last_success(conn)
            if good:
                st.write(f"対象{good['target_stores']}店舗 / 解析成功{good['successful_stores']}店舗 / 商品確認{good['stores_with_products']}店舗")
                st.write(f"商品詳細未確認: {good['unconfirmed_stores']}店舗。公式の提供店舗区分と、具体的な商品の確認を区別します。")
            st.caption(sync)
            for store in a.records(conn,'alcohol_stores').values():
                if not any(x['store_id']==store['store_id'] for x in rows):
                    st.markdown(f"[{store['name']}]({store['url']})：提供店舗・商品詳細未確認")
        mode=st.radio('探し方',['商品から探す','店舗から探す','更新履歴'],horizontal=True,key='alcohol_search_mode')
        if mode=='更新履歴':
            changes=list(a.records(conn,'alcohol_diffs').values())
            labels={'product_added':'商品追加','future_product':'販売開始予定','period_ended':'期間終了','price_changed':'価格変更','store_added':'販売店舗追加','store_removed':'掲載店舗削除・提供状況未確認','period_changed':'販売期間変更'}
            for change in sorted(changes,key=lambda x:x['detected_at_utc'],reverse=True)[:50]:
                st.write(f"{local(change['detected_at_utc'])} · {labels.get(change['kind'],change['kind'])} · {change['after']['name'] if change.get('after') else change['product_id']}")
            if not changes: st.info('更新差分はまだありません。')
            return
        query=st.text_input('商品名検索',key='alcohol_query')
        with st.expander('絞り込み',expanded=mode=='店舗から探す'):
            area_options=sorted({x['area'] for x in chosen})
            if 'alcohol_areas' in st.session_state: st.session_state['alcohol_areas']=[x for x in st.session_state['alcohol_areas'] if x in area_options]
            areas=st.multiselect('エリア',area_options,key='alcohol_areas')
            store_rows=[s for s in a.records(conn,'alcohol_stores').values() if not parks or s['park'] in parks]
            by_id={s['store_id']:s for s in store_rows}
            if 'alcohol_stores' in st.session_state: st.session_state['alcohol_stores']=[x for x in st.session_state['alcohol_stores'] if x in by_id]
            stores=st.multiselect('レストラン',list(by_id),format_func=lambda sid:by_id[sid]['name'],key='alcohol_stores')
            kind_options=sorted({x['kind'] for x in chosen})
            if 'alcohol_kinds' in st.session_state: st.session_state['alcohol_kinds']=[x for x in st.session_state['alcohol_kinds'] if x in kind_options]
            kinds=st.multiselect('アルコールの種類',kind_options,key='alcohol_kinds')
            price_filter=st.checkbox('価格帯で絞る（価格未確認は除外）',key='alcohol_price_enabled')
            max_price=st.number_input('上限価格（円）',min_value=100,max_value=100000,value=2000,step=100,key='alcohol_price') if price_filter else None
            limited=st.checkbox('期間限定商品のみ',key='alcohol_limited')
            within=st.checkbox('公式掲載上の販売期間内のみ',key='alcohol_within')
            new=st.checkbox('新しく追加された商品のみ（確認から7日以内）',key='alcohol_new')
        filtered=a.filter_catalog(rows,query,parks,areas,stores,kinds,max_price,limited,within,new)
        grouped={}
        for row in filtered: grouped.setdefault(row['product_id'],[]).append(row)
        st.caption(f'検索結果: {len(grouped)}商品 / {len(filtered)}店舗別の掲載')
        if not grouped:
            st.info('条件に合う商品がありません。未確認の内容は公式店舗ページでご確認ください。')
        items=sorted(grouped.items(),key=lambda pair:pair[1][0]['name'])
        total_pages=max(1,(len(items)+17)//18)
        if st.session_state.get('alcohol_page',1)>total_pages: st.session_state['alcohol_page']=1
        page=st.selectbox('表示ページ',range(1,total_pages+1),key='alcohol_page')
        for pid,sales in items[(page-1)*18:page*18]:
            first=sales[0]
            with st.container(border=True):
                st.markdown(f"**{first['name']}**")
                labels=[first['kind']]
                if first['new']: labels.append('新規確認')
                if first['limited']: labels.append('期間限定')
                st.caption(' · '.join(labels))
                for sale in sales:
                    price=sale['price_text'] or '価格未確認'
                    tax='（税込）' if sale.get('tax_included') and sale['price_text'] else ''
                    st.write(f"{price}{tax} · {'ランド' if sale['park']=='Disneyland' else 'シー'} / {sale['area']}")
                    st.markdown(f"[{sale['store_name']}]({sale['store_url']})")
                    st.caption(f"{sale['state']} · {sale['start'] or '開始日未確認'} ～ {sale['end'] or '終了日未確認'}")
                    for pause in sale.get('suspensions',[]): st.caption(f"公式掲載の販売休止: {pause['start']} ～ {pause['end']}")
                    if sale['admin_note']: st.caption('管理者による補足（公式情報とは別）: '+sale['admin_note'])
                st.markdown(f"[公式の商品・メニューページ]({first['url']})")
                all_sales=[x for x in rows if x['product_id']==pid]
                if len(all_sales)>len(sales):
                    with st.expander(f'この商品の全掲載店舗（{len(all_sales)}件）'):
                        for sale in all_sales: st.markdown(f"[{sale['store_name']}]({sale['store_url']}) · {sale['state']}")
                st.caption('確認: '+local(first['checked_at_utc']))
        if is_admin:
            with st.expander('管理者の補足・安全な手動更新'):
                st.markdown('[GitHubで週次調査を手動実行](https://github.com/sorapanel7533/disney-crowd-ai/actions/workflows/alcohol.yml)')
                st.caption('編集は週次データと別の補足として保持します。永続保存はJSONを手動実行のadmin_noteへ貼り付け、保存先からの受領を確認してください。')
                if rows:
                    keys={x['sale_key']:x for x in rows}
                    key=st.selectbox('補足する商品・店舗',list(keys),format_func=lambda key:keys[key]['name']+' / '+keys[key]['store_name'])
                    note=st.text_area('管理者による補足',max_chars=500)
                    if st.button('補足の保存用JSONを準備'):
                        import alcohol_admin
                        st.session_state['alcohol_note_export']=alcohol_admin.export(key,note,authorized=is_admin)
                    if st.session_state.get('alcohol_note_export'):
                        st.download_button('永続保存用JSONをダウンロード',json.dumps(st.session_state['alcohol_note_export'],ensure_ascii=False),file_name='alcohol-admin-note.json',mime='application/json')
    except Exception as exc:
        st.warning('アルコール情報を読み込めません。ほかの画面は引き続き利用できます。')
        st.caption('詳細: '+type(exc).__name__)
    finally:
        if conn is not None: conn.close()
