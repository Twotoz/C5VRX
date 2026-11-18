// C5VRX by Twotoz and the C5VRX contributors: TANG9K_DUAL mrc_core testbench.
// Streams raw_a.hex / raw_b.hex (one byte per line) through mrc_core, one
// sample per clock, and writes every output code to code.hex. run_tb.py
// compares it with fpga/model/mrc_model.py.
`timescale 1ns/1ps
module tb_mrc_core;
    reg clk = 0, rst = 1;
    reg [7:0] mem_a [0:2000000];
    reg [7:0] mem_b [0:2000000];
    reg [7:0] raw_a = 0, raw_b = 0;
    reg en_b;
    wire [5:0] code;
    integer n, k, f;
    mrc_core dut (.clk(clk), .rst(rst), .raw_a(raw_a), .raw_b(raw_b), .en_b(en_b), .code(code),
                  .w_a(), .w_b(), .theta());
    always #12.5 clk = ~clk;
    initial begin
        if (!$value$plusargs("n=%d", n)) n = 1000;
        if (!$value$plusargs("en_b=%d", k)) k = 1;
        en_b = k;
        $readmemh("raw_a.hex", mem_a);
        $readmemh("raw_b.hex", mem_b);
        f = $fopen("code.hex", "w");
        repeat (4) @(posedge clk);
        #1 rst = 0;
        for (k = 0; k < n + 40; k = k + 1) begin
            raw_a = (k < n) ? mem_a[k] : 8'h00;
            raw_b = (k < n) ? mem_b[k] : 8'h00;
            @(posedge clk);
            #1 $fwrite(f, "%02x\n", code);
        end
        $fclose(f);
        $finish;
    end
endmodule
