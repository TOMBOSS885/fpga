import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agent.trace import digest

DUT = 'module TopModule(input [7:0] a, output [7:0] y); assign y=~a; endmodule\n'
BAD = 'module TopModule(input [7:0] a, output [7:0] y); assign y=0; endmodule\n'
PROMPT = """Implement a module named TopModule with this interface:
 - input a (8 bits)
 - output y (8 bits)
The output is the bitwise inverse of a. Combinational logic only.
"""
TB = '''`timescale 1ns/1ps
module tb_self_check;
reg [7:0] a; wire [7:0] y_dut;
integer checks=0, errors=0, i;
TopModule arbitrary_instance(.a(a),.y(y_dut));
initial begin
  for(i=0;i<256;i=i+1) begin
    a=i; #1; checks=checks+1;
    if(y_dut !== ~a) errors=errors+1;
  end
  $display("TB_RESULT checks=%0d errors=%0d",checks,errors); $finish;
end
initial begin #10000; $display("TB_FAILURE: watchdog"); $finish; end
endmodule
'''


class TraceStub:
    def __init__(self):
        self.events = []

    def log(self, **fields):
        self.events.append(fields)


@contextmanager
def inference(content=DUT, status=200, slow=False, stream=False):
    records = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            records.append((self.command, self.path, dict(self.headers), b""))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"data": [{"id": "test-model"}]}).encode())

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            payload = json.loads(raw)
            records.append((self.command, self.path, dict(self.headers), raw))
            text = content(payload) if callable(content) else content
            self.send_response(status)
            self.send_header("Content-Type", "text/event-stream" if stream else "application/json")
            self.end_headers()
            if slow:
                try:
                    for _ in range(35):
                        self.wfile.write(b" "); self.wfile.flush(); time.sleep(0.1)
                except OSError:
                    pass
                return
            body = {"choices": [{"message": {"content": text}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 10}}
            if stream:
                for chunk in [text[:8], text[8:]]:
                    event = {"choices": [{"delta": {"content": chunk}}]}
                    self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
                self.wfile.write(b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
            else:
                self.wfile.write(json.dumps(body).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", records
    finally:
        server.shutdown(); server.server_close(); worker.join()
