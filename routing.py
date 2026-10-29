"""Time-constrained routes; durations are explicitly estimates, not measured walks."""
from itertools import permutations
import math
import pandas as pd


def minutes(value):
    if isinstance(value,str):
        h,m = map(int,value.split(':')[:2])
        return h*60+m
    return int(value)


def label(value):
    value = math.ceil(value)
    return f'{value//60:02d}:{value%60:02d}'


def wait_at(frame, attraction, minute):
    if frame is None or frame.empty or 'Attraction' not in frame:
        return None
    rows = frame[frame.Attraction.eq(attraction)].dropna(subset=['Predicted Wait']).copy()
    if rows.empty:
        return None
    rows['_minute'] = rows.Hour*60 + rows.get('Minute',0)
    if minute < rows._minute.min() or minute > rows._minute.max()+14:
        return None
    return float(rows.iloc[(rows._minute-minute).abs().argsort().iloc[0]]['Predicted Wait'])


def duration(attraction):
    # Broad experience estimates include boarding; displayed as assumptions.
    if any(x in attraction.lower() for x in ('railroad','riverboat','transit','gondola')):
        return 18
    if any(x in attraction.lower() for x in ('theater','room','philharmagic','encounter')):
        return 20
    if any(x in attraction.lower() for x in ('soaring','journey','beauty')):
        return 12
    return 8


def simulate(frame, order, start, end, area_for=lambda a:'', closed=()):
    current, total, previous = start, 0.0, None
    rows, skipped, warnings = [], [], []
    for a in order:
        if a in closed:
            skipped.append(a); warnings.append(f'{a}: 現在運休中（再開時刻不明）'); continue
        travel = 5 if previous is None or area_for(previous)==area_for(a) else 12
        queue_start = current + travel
        wait = wait_at(frame,a,queue_start)
        if wait is None:
            skipped.append(a); warnings.append(f'{a}: 予測不足'); continue
        ride = duration(a)
        finish = queue_start+math.ceil(wait)+ride
        if finish > end:
            skipped.append(a); warnings.append(f'{a}: 終了までに体験完了できません'); continue
        rows.append({'順番':len(rows)+1,'開始時刻':label(current),'Attraction':a,
                     '予測待ち時間':round(wait,1),'移動（推定分）':travel,'体験（推定分）':ride,
                     '移動・体験込み所要':finish-current,'終了時刻':label(finish)})
        current,total,previous = finish,total+wait,a
    return rows,total,current,warnings,skipped,[]


def optimize(frame, selected, start, end, area_for=lambda a:'', closed=()):
    selected = list(dict.fromkeys(selected))
    start,end = minutes(start),minutes(end)
    if end <= start or not selected:
        return pd.DataFrame(), {'total_wait':0,'end_time':label(start),'message':'施設と開始・終了時刻を確認してください',
                                'warnings':[],'skipped':selected,'filled_attractions':[], 'completed_count':0,'selected_count':len(selected)}
    baseline = simulate(frame,selected,start,end,area_for,closed)
    if len(selected)<=7:
        candidates = permutations(selected)
    else:
        # Bounded greedy candidates plus selection order; no factorial explosion.
        ranked = sorted(selected,key=lambda a: wait_at(frame,a,start+5) if wait_at(frame,a,start+5) is not None else float('inf'))
        candidates = [selected,ranked,list(reversed(ranked))]
    best,best_score = baseline,(-len(baseline[0]),baseline[1],baseline[2])
    for order in candidates:
        value = simulate(frame,order,start,end,area_for,closed)
        score = (-len(value[0]),value[1],value[2])
        if score < best_score:
            best,best_score=value,score
    rows,total,finish,warnings,skipped,filled=best
    return pd.DataFrame(rows), {'total_wait':round(total,1),'end_time':label(finish),
        'message':'体験完了数を優先し、予測待ち時間を比較。移動5/12分・体験8〜20分は推定です。',
        'warnings':warnings,'skipped':skipped,'filled_attractions':filled,'completed_count':len(rows),
        'selected_count':len(selected),'baseline_completed':len(baseline[0]),'baseline_wait':round(baseline[1],1),
        'saved_wait':round(baseline[1]-total,1) if len(baseline[0])==len(rows) else None}
