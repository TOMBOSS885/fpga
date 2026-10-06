module TopModule(input clk, input reset, input din, output reg pulse);
reg [3:0] history; integer received; always @(posedge clk) if(reset) begin history<=0; received<=0; pulse<=0; end else begin history<={history[2:0],din}; if(received<4) received<=received+1; pulse <= received>=3 && {history[2:0],din}==4'b0110; end
endmodule
