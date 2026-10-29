"""Name-matched third-party DPA reports. Never interpreted as official live sales."""
from datetime import date, datetime
import re
import pandas as pd
from lxml import html as html_parser

OFFICIAL_SOURCE='https://www.tokyodisneyresort.jp/tdr/guide/app_service/disneypremieraccess.html'
VERIFIED_ON='2026-10-30'


def eligible(park,day=None):
    day=day or date(2026,10,30)
    if park=='Disneyland':
        names=['Enchanted Tale of Beauty and the Beast','The Happy Ride with Baymax','Splash Mountain',
               'Big Thunder Mountain',"Pooh's Hunny Hunt",'Monsters, Inc. Ride & Go Seek!']
        if date(2026,9,15)<=day<=date(2027,1,7): names.append('Haunted Mansion')
        return names
    names=["Anna and Elsa's Frozen Journey","Rapunzel's Lantern Festival","Peter Pan's Never Land Adventure",
           'Soaring: Fantastic Flight','Toy Story Mania!','Tower of Terror','Journey to the Center of the Earth','Raging Spirits']
    if day>=date(2026,11,30): names.append('Indiana Jones Adventure: Temple of the Crystal Skull')
    return names


def parse_report(html,park,source,normalize,now):
    root=html_parser.fromstring(html)
    headline=' '.join(root.xpath('//h1//text() | //title/text()'))
    match=re.search(r'(20\d{2})[年/.-]\s*(\d{1,2})[月/.-]\s*(\d{1,2})',headline)
    if match:
        page_day=date(*map(int,match.groups()))
    else:
        # The real report has a dated <time> but an undated "today" title.
        # Use the source's timestamp, never the date of our later fetch.
        stamps=pd.to_datetime(root.xpath('//time/@datetime'),errors='coerce',utc=True)
        days={stamp.tz_convert('Asia/Tokyo').date() for stamp in stamps if not pd.isna(stamp)}
        if len(days)!=1:
            return pd.DataFrame(), 'ページの対象日を特定できないため保存しません'
        page_day=days.pop()
    if page_day>now.date(): return pd.DataFrame(),'未来日付の取得元ページは保存しません'
    records=[]
    for table in root.xpath('//table'):
        rows=table.xpath('.//tr')
        if not rows: continue
        first=[' '.join(cell.text_content().split()) for cell in rows[0].xpath('./th|./td')]
        if len(first)<3 or first[0]!='今日': continue
        names=[normalize(text) for text in first[1:]]
        allowed=set(eligible(park,page_day))
        if len(set(names)&allowed)<2: continue
        for row_index,tr in enumerate(rows):
            cells=[' '.join(c.text_content().split()) for c in tr.xpath('./th|./td')]
            if len(cells)!=len(first): continue
            if row_index==0:
                target=page_day
            else:
                dm=re.match(r'(\d{1,2})/(\d{1,2})',cells[0])
                if not dm: continue
                month,day=map(int,dm.groups())
                year=page_day.year-(month>page_day.month)
                try: target=date(year,month,day)
                except ValueError: continue
            if target>page_day: continue
            for name,value in zip(names,cells[1:]):
                if name not in set(eligible(park,target)): continue
                tm=re.search(r'(?<!\d)(\d{1,2}):(\d{2})(?!\d)',value)
                if not tm or (row_index==0 and '発行終了' not in value): continue
                h,m=map(int,tm.groups())
                if h>23 or m>59: continue
                records.append({'target_date':target,'attraction':name,'sellout_hour':h+m/60,
                                'status':'外部サイトの発行終了記録','source':source})
    frame=pd.DataFrame(records,columns=['target_date','attraction','sellout_hour','status','source'])
    return frame.drop_duplicates(['target_date','attraction']),f'施設名照合済み外部記録 {len(frame)}件（公式販売状況は未確認）'
