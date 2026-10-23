"""Synthetic evaluation fixtures only. Never imported by the solving agent."""
import argparse
import json
from pathlib import Path
import random
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent.tools import RtlToolchain

class OracleToolchain(RtlToolchain):
    def selftest(self, source, testbench, timeout):
        return self.run_sim(source, testbench.replace('SELFTEST_RESULT','TB_RESULT'),
                            tb_top='AgentSelfTest', timeout_s=timeout)


def cases():
    rng = random.Random(20261005)
    rows = []
    ports = 'input [7:0] a, input [7:0] b, input [1:0] sel, output [7:0] y'
    spec = 'Combinational unsigned 8-bit ALU: sel=0 gives a XOR b; sel=1 gives a AND b; sel=2 rotates a left by one bit (wrap bit 7 to bit 0); sel=3 gives the larger unsigned operand (a on equality).'
    source = 'assign y=sel==0 ? a^b : sel==1 ? a&b : sel==2 ? {a[6:0],a[7]} : a>=b ? a:b;'
    vectors = []
    for i in range(320):
        a,b,s = rng.randrange(256),rng.randrange(256),i%4
        y = (a^b,a&b,((a<<1)|(a>>7))&255,max(a,b))[s]
        vectors.append(('a=8\'d%d; b=8\'d%d; sel=2\'d%d;'%(a,b,s),y))
    rows.append(('h01_alu',ports,spec,source,vectors,False,8))

    ports='input signed [7:0] a, input signed [7:0] b, output signed [7:0] y'
    spec='Combinational signed saturating addition. Interpret a and b as signed two-complement 8-bit values. Compute their mathematical sum and clamp to [-128,127]. Output the clamped signed 8-bit value y. No clock.'
    source="wire signed [8:0] sum; assign sum={a[7],a}+{b[7],b}; assign y=sum>9'sd127 ? 8'sd127 : sum < -9'sd128 ? 8'h80 : sum[7:0];"
    vectors=[]
    pairs=[(a,b) for a in (-128,-127,-1,0,1,126,127) for b in (-128,-127,-1,0,1,126,127)]
    pairs += [(rng.randrange(-128,128),rng.randrange(-128,128)) for _ in range(256)]
    for a,b in pairs:
        vectors.append(("a=8'd%d; b=8'd%d;"%(a&255,b&255),max(-128,min(127,a+b))&255))
    rows.append(('h02_saturating_add',ports,spec,source,vectors,False,8))

    ports='input clk, input reset, input load, input [3:0] value, input up, input down, output reg [3:0] y'
    spec='A 4-bit unsigned saturating counter updated on each rising clk edge. Active-high synchronous reset sets y=0 and has highest priority. Otherwise load sets y=value. Otherwise up=1/down=0 increments unless y=15; up=0/down=1 decrements unless y=0. Both equal means hold. Output y is registered.'
    source="always @(posedge clk) if(reset) y<=0; else if(load) y<=value; else if(up && !down && y!=15) y<=y+1'b1; else if(down && !up && y!=0) y<=y-1'b1;"
    vectors=[]; y=0
    for i in range(320):
        reset=int(i in (0,67,199)); load=int(i%29==1); value=rng.randrange(16)
        up,down=((1,0) if i%80<25 else (0,1) if i%80<50 else (rng.randrange(2),rng.randrange(2)))
        y=0 if reset else value if load else min(15,y+1) if up and not down else max(0,y-1) if down and not up else y
        vectors.append(('reset=%d; load=%d; value=4\'d%d; up=%d; down=%d;'%(reset,load,value,up,down),y))
    rows.append(('h03_priority_counter',ports,spec,source,vectors,True,4))

    ports='input clk, input reset, input en, input [7:0] din, output reg [7:0] y'
    spec='Two-stage 8-bit enabled pipeline on rising clk edges. Active-high synchronous reset clears BOTH stages to zero even when en=0. If en=1, stage1 captures din and y captures the OLD stage1 value from before that edge. If en=0 both stages hold. y is the second stage.'
    source='reg [7:0] stage1; always @(posedge clk) if(reset) begin stage1<=0; y<=0; end else if(en) begin stage1<=din; y<=stage1; end'
    vectors=[]; stage=0; y=0
    for i in range(280):
        reset=int(i in (0,71,170)); en=rng.randrange(2); din=rng.randrange(256)
        if reset: stage,y=0,0
        elif en: stage,y=din,stage
        vectors.append(('reset=%d; en=%d; din=8\'d%d;'%(reset,en,din),y))
    rows.append(('h04_enabled_pipeline',ports,spec,source,vectors,True,8))

    for index,pattern in enumerate(('0110','1001001'),5):
        n=len(pattern)
        ports='input clk, input reset, input din, output reg pulse'
        spec='The module should detect the sequence %s in a serial bit stream. Input is sampled on the positive edge. The most recent input bits, including the one sampled on the current cycle, match the sequence; otherwise it is 0. Overlapping occurrences count separately. reset is an active-high synchronous reset. pulse is a registered output. After a reset, pulse stays 0 until %d input bits have been received.'%(pattern,n)
        source="reg [%d:0] history; integer received; always @(posedge clk) if(reset) begin history<=0; received<=0; pulse<=0; end else begin history<={history[%d:0],din}; if(received<%d) received<=received+1; pulse <= received>=%d && {history[%d:0],din}==%d'b%s; end"%(n-1,n-2,n,n-1,n-2,n,pattern)
        bits=[int(x) for x in pattern*5]+[rng.randrange(2) for _ in range(300)]
        vectors=[]; history=''
        for i,bit in enumerate(bits):
            reset=int(i in (0,93,201)); history='' if reset else history+str(bit)
            expected=int(len(history)>=n and history.endswith(pattern))
            vectors.append(('reset=%d; din=%d;'%(reset,bit),expected))
        rows.append(('h%02d_pattern_%s'%(index,pattern),ports,spec,source,vectors,True,1))
    return rows


def build(root):
    root.mkdir(parents=True,exist_ok=False)
    health=[]; tools=OracleToolchain()
    for name,ports,spec,body,vectors,sequential,width in cases():
        task=root/name; task.mkdir()
        declarations=[]; connections=[]
        import re
        for port in ports.split(','):
            port=port.strip(); direction=port.split()[0]; ident=port.split()[-1]
            shape=re.search(r'\[[^]]+\]',port)
            declarations.append('%s %s %s;' % ('wire' if direction=='output' else 'reg',shape.group() if shape else '',ident))
            connections.append('.%s(%s)'%(ident,ident))
        output='pulse' if width==1 else 'y'
        stimuli=[]
        for drive,expected in vectors:
            timing='#2; clk=1; #1;' if sequential else '#1;'
            tail='#2; clk=0;' if sequential else ''
            stimuli.append("%s %s if(%s !== %d'd%d) errors=errors+1; checks=checks+1; %s"%(drive,timing,output,width,expected,tail))
        tb='''`timescale 1ns/1ps
module AgentSelfTest;
%s
integer errors=0,checks=0;
TopModule dut(%s);
initial begin
%s
%s
$display("SELFTEST_RESULT checks=%%0d errors=%%0d",checks,errors);
$display("Mismatches: %%0d in %%0d samples",errors,checks); $finish;
end endmodule
'''%('\n'.join(declarations),','.join(connections),'clk=0;' if sequential else '', '\n'.join(stimuli))
        if name in ('h03_priority_counter','h04_enabled_pipeline'):
            tb=tb.replace('integer errors=0,checks=0;', 'integer errors=0,checks=0; reg [%d:0] held;'%(width-1))
            tb=tb.replace('checks=checks+1;', 'checks=checks+1; held=y;')
            tb=tb.replace('reset=1;', 'reset=1; #1; if(checks>0) begin if(y !== held) errors=errors+1; checks=checks+1; end')
        candidate='module TopModule(%s);\n%s\nendmodule\n'%(ports,body)
        # Independently computed Python outputs validate the SV positive fixture.
        rc,log=tools.selftest(candidate,tb,60)
        (task/'fixture_check.log').write_text(log,encoding='utf-8')
        mutant='module TopModule(%s); always @* %s=0; endmodule'%(ports,output) if 'output reg' in ports else 'module TopModule(%s); assign %s=0; endmodule'%(ports,output)
        # Use continuous assignment with a wire output for a deterministic stuck-low mutant.
        mutant='module TopModule(%s); assign %s=0; endmodule'%(ports.replace('output reg','output wire'),output)
        mutant_rc,mutant_log=tools.selftest(mutant,tb,60)
        (task/'mutation_check.log').write_text(mutant_log,encoding='utf-8')
        health.append(dict(task=name,positive_rc=rc,stuck_zero_rc=mutant_rc,samples=len(vectors)))
        if rc!=0 or mutant_rc!=1:
            raise RuntimeError('Invalid fixture: '+name+' '+str(health[-1]))
        (task/'prompt.txt').write_text('Implement a module named TopModule.\n'+spec,encoding='utf-8')
        (task/'interface.txt').write_text('module TopModule(%s);\nendmodule\n'%ports,encoding='utf-8')
        (task/'tb.sv').write_text(tb,encoding='utf-8')
        (task/'ref.sv').write_text('// Oracle outputs are independently computed Python literals in tb.sv.\n',encoding='utf-8')
        (task/'positive_fixture.sv').write_text(candidate,encoding='utf-8')
        (task/'task.json').write_text(json.dumps(dict(task_id=name,top='TopModule',part='xczu3eg-sbva484-1-e',period_ns=5,reference_module='ref.sv',testbench='tb.sv',tb_top='AgentSelfTest',extra_files=[]),indent=2),encoding='utf-8')
        print('Validated '+name,flush=True)
    (root/'fixture_health.json').write_text(json.dumps(dict(development_only=True,seed=20261005,checks=health),indent=2),encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output',required=True)
    build(Path(parser.parse_args().output).resolve())
