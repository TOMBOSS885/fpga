module TopModule(input clk, input reset, input din, output reg pulse);
reg [6:0] history; integer received; always @(posedge clk) if(reset) begin history<=0; received<=0; pulse<=0; end else begin history<={history[5:0],din}; if(received<7) received<=received+1; pulse <= received>=6 && {history[5:0],din}==7'b1001001; end
endmodule
