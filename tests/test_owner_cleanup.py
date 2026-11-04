"""One-time administrative cleanup, with real append/reducer/display/seed replay. No network."""
import datetime,json,os,sys,tempfile,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import events as E, reconcile as R, owner_cleanup as O, drops as D
NOW=E.parse('2026-11-04T19:15:00Z'); OLD=E.parse('2026-11-04T17:00:00Z')
base={'status':'closed','fullClosure':True,'road':'Main St','spot':'at Oak Ave','where':'Main St at Oak Ave','lat':28.2,'lng':-80.7,'title':'Road closure','reported':E.iso(OLD),'updated':E.iso(OLD)}
manual=dict(base,id='man-old',layer='manual')
feed=dict(base,id='fl511-1',layer='Closures',road='Other Rd',spot='at Remote St',where='Other Rd at Remote St',lat=28.6)
obs={'generated':E.iso(NOW),'sources':{'fhp':'up','fl511':'up','manual':'up'},'items':[manual,feed]}

def check(name,ok):
 print(('ok ' if ok else 'FAIL ')+name)
 if not ok:raise AssertionError(name)

def seed(path,ob=obs):
 n,_=R.plan(ob,E._empty(),E.iso(OLD));E.append(n,path,clearance_enabled=True,now=OLD)
 return E.derive(E.load(path),NOW)

with tempfile.TemporaryDirectory() as tmp:
 path=os.path.join(tmp,'events.jsonl');marker=os.path.join(tmp,'audit','cleanup.json')
 st=seed(path);rid='brevard:manual:old';prefix=Path(path).read_bytes()
 for health in ['down','partial','empty']:
  bad=dict(obs,sources=dict(obs['sources'],fhp=health))
  w,rec=O.execute(bad,st,NOW,'brevard',path,marker)
  check('no cleanup when a feed is '+health,not w and rec is None and not os.path.exists(marker))
 w,rec=O.execute(dict(obs,generated=E.iso(OLD)),st,NOW,'brevard',path,marker)
 check('stale successful observations cannot trigger cleanup',not w and rec is None)
 w,rec=O.execute(obs,st,NOW,'nj-hudson',path,marker)
 check('never applies outside approved FL counties',not w and rec is None)
 # Reducer enforces health and counterparts even if a clear event is supplied directly.
 raw={'kind':'cleared','rid':rid,'src':'manual','actor':'owner-cleanup','stime':E.iso(NOW),
      'evidence':{'kind':'owner-cleanup','cutoff':O.CUTOFF,'rule':O.RULE,'checkedAt':E.iso(NOW),'reopenedAt':E.iso(NOW)},'at':E.iso(NOW)}
 bad=copy.deepcopy(st);bad['sources']['fhp']['state']='down';E.apply(bad,raw)
 check('reducer refuses unhealthy cleanup',bad['records'][rid]['status']=='active')
 for near in [dict(base),dict(base,where='Neighbor St at Elm St',road='Neighbor St',spot='at Elm St',lat=28.2005)]:
  nearstate=copy.deepcopy(st);fr=next(x for x in nearstate['records'].values() if x['src']=='fl511');fr['fields']=near
  check('current or nearby uncertain feed counterpart keeps warning',not O.safe_to_remove(nearstate['records'][rid],nearstate))
  E.apply(nearstate,raw);check('reducer also keeps counterpart warning',nearstate['records'][rid]['status']=='active')
 w,rec=O.execute(obs,st,NOW,'brevard',path,marker)
 check('old non-feed closure removed once with bounded owner receipt',len(w)==1 and rec['removed']==[rid])
 check('accepted history is unchanged prefix',Path(path).read_bytes().startswith(prefix))
 after=E.derive(E.load(path),NOW);items,green=R.display(after)
 check('administrative removal shows no pin or green/reopened badge',rid not in [x['id'] for x in items+green] and after['records'][rid]['show']=='hidden')
 n,_=R.plan(obs,after,E.iso(NOW+datetime.timedelta(minutes=15)));E.append(n,path,clearance_enabled=True,now=NOW+datetime.timedelta(minutes=15))
 after=E.derive(E.load(path),NOW+datetime.timedelta(minutes=15))
 check('old seeded manual entry does not revive',after['records'][rid]['status']=='reopened')
 w,again=O.execute(obs,after,NOW+datetime.timedelta(days=1),'brevard',path,marker)
 check('completed batch does not remove retained reports later',not w and again==rec)
 # A real new report after the decision, even before cleanup execution, must reactivate.
 fresh=dict(manual,updated='2026-11-04T19:00:00Z',reported='2026-11-04T19:00:00Z')
 n,_=R.plan(dict(obs,items=[fresh,feed]),after,E.iso(NOW+datetime.timedelta(minutes=30)))
 E.append(n,path,clearance_enabled=True,now=NOW+datetime.timedelta(minutes=30))
 check('genuinely new source report after cutoff can pin',E.derive(E.load(path),NOW)['records'][rid]['status']=='active')
 # Historical contrary evidence remains available to the drop trial after administrative removal.
 recold=st['records'][rid];E.apply(st,raw)
 ep={'rid':'brevard:fhp:x','missingSince':'2026-11-04T18:00:00Z','fields':dict(base),'src':'fhp'}
 found,_=D.counterparts(ep,[],st)
 check('drop trial keeps old evidence with administrative context',any(x['rid']==rid and not x['onMap'] and 'owner' in x['listing'] for x in found))
 # Newer/nonflood reports are not targets.
 for patch in [{'lastReported':'2026-11-04T19:00:00Z'}, {'firstReported':'2026-11-04T19:00:00Z'}, {'src':'fhp'},
               {'fields':dict(base,status='mvc',fullClosure=False,title='MVC')}]:
  record=dict(recold,**patch);check('fresh/feed/non-flood report excluded',not O.eligible(record))
 # A crash after the event append but before receipt write cannot start another batch.
 os.remove(marker);w,recovered=O.execute(obs,E.derive(E.load(path),NOW),NOW,'brevard',path,marker)
 check('recovery after append never starts another batch',not w and recovered['recoveredAfterInterruptedBatch'] and recovered['before'] is None and rid in recovered['removed'])
with tempfile.TemporaryDirectory() as tmp:
 path=os.path.join(tmp,'events.jsonl');marker=os.path.join(tmp,'audit/cleanup.json')
 near=dict(obs,items=[manual,dict(feed,lat=28.2005)])
 st=seed(path,near);w,rec=O.execute(near,st,NOW,'brevard',path,marker)
 check('zero-removal healthy batch still completes once',not w and rec['removed']==[])
 n,_=R.plan(dict(obs,items=[manual]),st,E.iso(NOW));E.append(n,path,clearance_enabled=True,now=NOW)
 w,rec2=O.execute(obs,E.derive(E.load(path),NOW),NOW+datetime.timedelta(days=1),'brevard',path,marker)
 check('counterpart disappearing later does not turn cleanup into a standing rule',not w and rec2==rec)
 bad=dict(raw,evidence=dict(raw['evidence'],cutoff='2026-11-05T00:00:00Z'))
 try:E.append([bad],path,clearance_enabled=True,now=NOW)
 except E.Refused:check('unapproved cutoff rejected by event validation',True)
 else:raise AssertionError('bad cutoff accepted')
print('Owner cleanup checks passed')
