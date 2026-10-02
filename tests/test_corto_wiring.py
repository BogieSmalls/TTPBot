import copy
import logging
import tempfile
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from ttpbot.autumn.wiring import build_autumn_runner
from ttpbot.autumn.engine import engine_from_env
from ttpbot.autumn.matching import RaceIdentity
from ttpbot.autumn.rooms import room_title, recover_autumn_room
from ttpbot.autumn.broadcast_request import build_broadcast_request
from ttpbot.autumn.schedule import ScheduleRow
from ttpbot.provider import RacetimeProvider
from ttpbot.handler import TTPRaceHandler
from ttpbot.bot import TTPBot
from tests.test_autumn_adapters import race
LOG=logging.getLogger('tc-test')
ENV={'Z1RR_ENGINE_TOKEN':'scratch','Z1RR_CORTO_ENABLED':'true','Z1RR_CORTO_EDITION':'33'}

def tc_race():
 r=race();return replace(r,identity=RaceIdentity('corto','W1-1',1,'33'),runner_one='BrewersFanJP',runner_two='chessjerk')

class CortoWiringTests(unittest.IsolatedAsyncioTestCase):
 def test_own_edition_and_sheet_never_fall_back_to_autumn(self):
  env=dict(ENV,Z1RR_AUTUMN_EDITION='2026',Z1RR_AUTUMN_SCHEDULE_URL='https://autumn.invalid')
  bot=SimpleNamespace(provider=RacetimeProvider('https://racetime.gg','z1r'),access_token='scratch')
  runner=build_autumn_runner(env,bot,LOG,event='corto')
  self.assertTrue(runner.configured)
  self.assertEqual(runner.engine.edition,'33')
  self.assertEqual(runner.scheduler.event,'corto')
  self.assertIn('1eZsfEjRi0ni9aE0GbQSDrEOL178jVd7h-P-StsNO_AQ',runner.scheduler.source.url)
  self.assertIsNone(engine_from_env({'Z1RR_ENGINE_TOKEN':'scratch'},event='corto'))

 def test_tc_room_recognition_and_booth_title(self):
  from ttpbot import room_policy
  r=tc_race();title=room_title(r)
  self.assertEqual(title,'Torneo Corto #33 \u2014 BrewersFanJP vs chessjerk [W1-1]')
  self.assertTrue(room_policy.is_corto_room({'goal':{'name':'Beat the game'},'info_user':title}))
  self.assertFalse(room_policy.is_autumn_room({'goal':{'name':'Beat the game'},'info_user':title}))
  row=ScheduleRow(r.at,'BrewersFanJP','chessjerk','','','','Z1Rracing')
  doc={'event':'corto','format':'single','seeds':list(range(8)),'ranks':{'BrewersFanJP':1,'chessjerk':8},'racetimeIds':{'BrewersFanJP':'one','chessjerk':'two'},'twitchChannels':{'BrewersFanJP':'brewersfanjp','chessjerk':'chessjerk'}}
  payload=build_broadcast_request(r,row,'https://racetime.gg/z1r/tc-room',doc,None,LOG,edition='33')
  self.assertEqual(payload['title'],'Torneo Corto #33\nQuarterfinals')
  self.assertEqual(payload['requestKey'],'tournament:corto:33:W1-1:game:1')
  self.assertEqual([p['tournamentSeed'] for p in payload['racers']],['#1','#8'])

 async def test_tc_finished_room_goes_only_to_tc_result_collector(self):
  handler=TTPRaceHandler(conn=None,logger=LOG,state={})
  handler.corto_room=True;handler.data={'name':'z1r/tc-room'}
  handler.corto_results=SimpleNamespace(record=AsyncMock())
  handler.autumn_results=SimpleNamespace(record=AsyncMock())
  await handler.end()
  handler.corto_results.record.assert_awaited_once_with(handler.data)
  handler.autumn_results.record.assert_not_awaited()

 def test_bot_builds_tc_with_separate_hardened_state_files(self):
  with tempfile.TemporaryDirectory() as root, patch.dict('os.environ',ENV,clear=True):
   bot=TTPBot.__new__(TTPBot);bot.data_dir=root;bot.provider=RacetimeProvider('https://racetime.gg','z1r');bot.logger=LOG;bot.state={}
   try:
    runner=bot._build_corto_runner()
    self.assertIsNotNone(runner)
    self.assertEqual(runner.engine.edition,'33')
    self.assertEqual(runner.scheduler.created_store.path.name,'corto_created_races.json')
    self.assertEqual(runner.results.store.path.name,'corto_results.json')
    self.assertEqual(runner.results.scope,'corto-33')
   finally:TTPRaceHandler.corto_results=None

 async def test_recovery_cannot_attach_an_autumn_room_to_tc(self):
  r=tc_race();r.room_marker='Z1RR:scratch'
  with patch('ttpbot.autumn.rooms._recover',new_callable=AsyncMock,return_value=None) as recover:
   await recover_autumn_room(r,RacetimeProvider('https://racetime.gg','z1r'),'token',LOG)
   titles=recover.call_args.args[3]
   self.assertTrue(all('Autumn' not in title for title in titles))
class CortoNightTests(unittest.IsolatedAsyncioTestCase):
 async def test_tc_schedule_room_invites_restart_and_council_result_use_the_real_engine(self):
  import asyncio,json,os,subprocess
  from pathlib import Path
  from datetime import timedelta
  from ttpbot.state import DestinationStateStore
  from tests.test_autumn_scheduler import FakeSource,HEADER,START,at,row
  engine_root=os.environ.get('Z1RR_ENGINE_DIR')
  if not engine_root:self.skipTest('set Z1RR_ENGINE_DIR for the real engine')
  temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);root=Path(engine_root).resolve();clock=Path(temp.name)/'clock.txt';clock.write_text(at(START,40).isoformat())
  code='''
import {readFileSync} from 'node:fs';
import {createEngineService} from SERVICE;
import {createTournamentWriter} from WRITER;
import {createTournamentStore} from STORE;
const storeFor=event=>createTournamentStore({event,dir:DIR});
const writer=createTournamentWriter({storeFor,now:()=>new Date(readFileSync(CLOCK,'utf8'))});
const server=createEngineService({token:'scratch',storeFor,writer});
server.listen(0,'127.0.0.1',()=>console.log(server.address().port));
'''
  for key,value in {'SERVICE':(root/'src/service.mjs').as_uri(),'WRITER':(root/'src/writer.mjs').as_uri(),'STORE':(root/'src/store.mjs').as_uri(),'DIR':str(Path(temp.name)/'engine'),'CLOCK':str(clock)}.items():code=code.replace(key,json.dumps(value))
  process=await asyncio.create_subprocess_exec('node','--input-type=module','--eval',code,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
  async def stop():
   if process.returncode is None:process.terminate();await asyncio.wait_for(process.wait(),5)
  self.addAsyncCleanup(stop);port=int(await asyncio.wait_for(process.stdout.readline(),10))
  env=dict(ENV,Z1RR_ENGINE_URL='http://127.0.0.1:{}'.format(port));bot=SimpleNamespace(provider=RacetimeProvider('https://racetime.gg','z1r'),access_token='scratch',state={})
  stores={kind:DestinationStateStore(kind.replace('autumn_','corto_')+'.json',bot.provider.destination_key,kind,data_dir=temp.name) for kind in ['autumn_bindings','autumn_created_races','autumn_mirrored_times','autumn_sent_webhooks','autumn_booth_notices','autumn_results']}
  runner=build_autumn_runner(env,bot,LOG,stores,event='corto');engine=runner.engine
  names=['BrewersFanJP','Bogie','cUstOm','Cfalcon','equations19','Eatmysteel','syscrusher','chessjerk']
  for op,body in [('draw',{'seeds':names,'format':'single'}),('racetime',{'racetimeIds':{'BrewersFanJP':'one','chessjerk':'two'}}),('migrate',{'edition':'33'})]:self.assertTrue((await engine._post(op,body)).ok)
  source=FakeSource(HEADER+'\n'+row(START,'(1) BrewersFanJP','(8) chessjerk'))
  runner.scheduler.source=source
  room='https://racetime.gg/z1r/tc-scratch'
  with patch('ttpbot.autumn.wiring.create_autumn_room',new_callable=AsyncMock,return_value=room) as create:
   await runner.scheduler.tick(at(START,40))
   self.assertEqual((await engine.state())['document']['times']['W1-1']['at'],START.isoformat())
   later=START+timedelta(minutes=10);source.csv_text=HEADER+'\n'+row(later,'(1) BrewersFanJP','(8) chessjerk')
   clock.write_text(at(later,25).isoformat())
   await runner.scheduler.tick(at(later,25));create.assert_awaited_once()
   self.assertEqual(bot.state['z1r/tc-scratch']['autumn_race']['invite'],['one','two'])
   again=build_autumn_runner(env,bot,LOG,stores,event='corto');again.scheduler.source=source
   await again.scheduler.tick(at(later,24));create.assert_awaited_once()
  saved=(await engine.state())['document'];self.assertEqual(len(saved['rooms']),1)
  self.assertEqual(len([a for a in saved['actions'].values() if a['kind']=='room-announcement']),1)
  handler=TTPRaceHandler(conn=None,logger=LOG,state={});handler.corto_room=True;handler.corto_results=again.results
  handler.data={'name':'z1r/tc-scratch','status':{'value':'finished'},'entrants':[{'user':{'id':'one','name':'BrewersFanJP'},'status':{'value':'done'},'finish_time':'PT1H'},{'user':{'id':'two','name':'chessjerk'},'status':{'value':'dnf'},'finish_time':None}]}
  await handler.end();saved=(await engine.state())['document'];self.assertEqual(saved['results'],{})
  proposal=next(iter(saved['proposals'].values()));self.assertEqual(proposal['status'],'pending');self.assertEqual(proposal['facts']['winner'],'BrewersFanJP')
  self.assertTrue((await engine._post('decideResult',{'edition':'33','proposalId':proposal['id'],'proposalRevision':proposal['revision'],'decision':'confirm','decisionId':'tc-council','actor':'council'})).ok)
  draw=await engine.draw();self.assertEqual(next(m for m in draw['matches'] if m['id']=='W2-1')['a'],'BrewersFanJP')
  self.assertEqual((await engine.state())['document']['autonomy']['level'],'hold-results')
