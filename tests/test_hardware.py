"""Execute the actual firmware C++ with a deterministic clock and Serial input."""
from pathlib import Path
import shutil
import subprocess

import pytest

from adaptive_traffic.config import Config
from adaptive_traffic.controllers import CONTROLLERS
from adaptive_traffic.scenarios import create_traffic_plan
from adaptive_traffic.simulation import Simulation

ROOT=Path(__file__).parents[1]


@pytest.fixture(scope='module')
def firmware(tmp_path_factory):
    compiler=shutil.which('g++')
    if not compiler: pytest.skip('g++ needed to execute the real firmware host simulator')
    binary=tmp_path_factory.mktemp('firmware')/'simulator'
    subprocess.run([compiler,'-std=c++11','-Wall','-Wextra','-Werror',str(ROOT/'hardware/simulator.cpp'),'-o',str(binary)],check=True)
    return binary


class Script:
    def __init__(self):
        self.lines=['HELLO']; self.seq=0; self.now=0
    def send(self, verb, *args):
        self.seq+=1
        line=','.join(map(str,(verb,self.seq,*args))); self.lines.append(line); return line
    def wait(self, ms, ping=True):
        while ms:
            step=min(1000,ms);self.now=(self.now+step)&0xffffffff;ms-=step
            self.lines.append('@'+str(self.now))
            if ping: self.send('PING')
    def sync(self):
        self.send('SYNC');self.wait(4100);self.send('STATE',0,'NS_GREEN')
    def run(self, binary):
        result=subprocess.run([str(binary)],input='\n'.join(self.lines)+'\n',text=True,capture_output=True,check=True)
        for line in result.stdout.splitlines():
            if not line.startswith('PINS,'): continue
            mask=int(line.split(',')[1]); lights=[(mask>>(3*i))&7 for i in range(4)]
            assert all(v in (1,2,4) for v in lights)
            assert lights[0]==lights[1] and lights[2]==lights[3]
            assert not (lights[0]==4 and lights[2]==4)
            if mask&(1<<13): assert lights==[1,1,1,1] and not mask&(1<<12)
        return result.stdout


@pytest.mark.parametrize('controller',CONTROLLERS)
def test_real_model_replays_on_real_firmware(firmware,controller):
    config=Config(simulation_duration=120)
    sim=Simulation(config,create_traffic_plan('NS_HEAVY',42,120),CONTROLLERS[controller](config),(5,65))
    script=Script();script.sync()
    while not sim.finished:
        sim.step();script.wait(1010);script.send('STATE',int(sim.time*1000),sim.light.state.value)
    output=script.run(firmware)
    assert 'ERROR' not in output
    assert 'PED_GREEN' in output and 'EW_GREEN' in output


@pytest.mark.parametrize('phase',['EW_GREEN','PED_GREEN','ALL_RED_TO_EW','INVALID'])
def test_skipped_states_fail_closed(firmware,phase):
    s=Script();s.sync();s.send('STATE',1000,phase)
    out=s.run(firmware)
    assert 'ERROR,TRANSITION' in out and out.strip().endswith('SAFE_ALL_RED')


def test_short_yellow_and_unconfirmed_resync_fail(firmware):
    s=Script();s.send('SYNC');s.send('STATE',0,'NS_GREEN')
    assert 'ERROR,TRANSITION' in s.run(firmware)
    s=Script();s.sync();s.send('STATE',1000,'NS_YELLOW');s.wait(2999)
    s.send('STATE',2000,'ALL_RED_TO_EW')
    assert 'ERROR,TRANSITION' in s.run(firmware)


def test_duplicate_has_no_repeat_or_timer_reset(firmware):
    s=Script();s.sync();line=s.send('STATE',1000,'NS_YELLOW')
    s.wait(2000,ping=False);s.lines.append(line)
    s.wait(1000);s.send('STATE',2000,'ALL_RED_TO_EW')
    out=s.run(firmware)
    assert 'ERROR' not in out and out.strip().endswith('ALL_RED_TO_EW')


@pytest.mark.parametrize('bad',['STATE,99,1000,NS_GREEN','STATE,0,1000,NS_GREEN','PING,abc',
                               'PING,4294967296','X'*100,'BOGUS,9','STATE,7,,NS_GREEN'])
def test_malformed_and_overflow_fail_closed(firmware,bad):
    s=Script();s.sync();s.lines.append(bad);out=s.run(firmware)
    assert 'ERROR,' in out and out.strip().endswith('SAFE_ALL_RED')


def test_watchdog_and_recovery_require_new_handshake(firmware):
    s=Script();s.sync();s.wait(2500,ping=False);s.send('STATE',1000,'NS_GREEN')
    assert 'ERROR,TIMEOUT' in s.run(firmware)
    s.lines.append('HELLO');s.seq=0;s.sync()
    out=s.run(firmware)
    assert out.strip().endswith('NS_GREEN')


def test_button_debounce_one_event_until_release(firmware):
    s=Script();s.lines+=['!1','@10','!0','@20','!1','@59','@60','@100','!1','@150','!0','@190','!1','@230']
    out=s.run(firmware)
    assert out.count('BUTTON,PED')==2
    assert 'GREEN' not in out # press is only an event, never a green command


def test_millis_wrap_does_not_bypass_guards(firmware):
    s=Script();s.lines=['@4294965000','HELLO'];s.now=4294965000;s.sync()
    out=s.run(firmware)
    assert 'ERROR' not in out and out.strip().endswith('NS_GREEN')


def test_time_jump_is_not_fast_forward(firmware):
    s=Script();s.sync();s.send('STATE',100000,'NS_GREEN')
    assert 'ERROR,MODEL_TIME' in s.run(firmware)


def test_partial_line_timeout_closes_green(firmware):
    s=Script();s.sync();s.lines.append('%STATE,');s.wait(500,ping=False)
    out=s.run(firmware)
    assert 'ERROR,PARTIAL_LINE' in out and out.strip().endswith('SAFE_ALL_RED')


def test_changed_duplicate_sequence_fails(firmware):
    s=Script();s.sync();s.lines.append(f'STATE,{s.seq},1000,EW_GREEN')
    assert 'ERROR,SEQUENCE' in s.run(firmware)


def test_real_pin_mapping_and_protocol_timings_match_model():
    config=Config()
    assert [config.yellow,config.all_red,config.ped_green,config.ped_clearance]==[3,1,10,3]
    source=(ROOT/'hardware/arduino/AdaptiveTrafficArduino/AdaptiveTrafficArduino.ino').read_text()
    assert '{2,3,4,5,6,7,8,9,10,11,12,A0,A1,A2}' in source
    assert 'BUTTON_PIN = A3' in source and 'INPUT_PULLUP' in source
