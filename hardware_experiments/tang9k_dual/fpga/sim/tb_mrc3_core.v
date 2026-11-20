// C5VRX by Twotoz and the C5VRX contributors: Tripleversity mrc3_core
// testbench. Streams raw_a/b/c.hex (one six-bit sample per line) through
// mrc3_core, one sample per clock, and writes every code to code.hex.
// run_tb3.py compares it with fpga/model/mrc3_model.py.
`timescale 1ns/1ps
module tb_mrc3_core;
    reg clk = 0, rst = 1;
    reg [5:0] mem_a [0:2000000];
    reg [5:0] mem_b [0:2000000];
    reg [5:0] mem_c [0:2000000];
    reg [2:0] mem_v [0:2000000];
    reg [5:0] raw_a = 0, raw_b = 0, raw_c = 0;
    reg [2:0] vv = 3'b111;
    reg en_b, en_c;
    wire [5:0] code;
    integer n, k, f;
    mrc3_core dut (.clk(clk), .rst(rst), .raw_a(raw_a), .raw_b(raw_b), .raw_c(raw_c),
                   .en_b(en_b), .en_c(en_c),
                   .va(vv[0]), .vb(vv[1]), .vc(vv[2]), .code(code));
    always #12.5 clk = ~clk;
    initial begin
        if (!$value$plusargs("n=%d", n)) n = 1000;
        if (!$value$plusargs("en_b=%d", k)) k = 1;
        en_b = k;
        if (!$value$plusargs("en_c=%d", k)) k = 1;
        en_c = k;
        $readmemh("raw_a.hex", mem_a);
        $readmemh("raw_b.hex", mem_b);
        $readmemh("raw_c.hex", mem_c);
        $readmemh("valid.hex", mem_v);
        f = $fopen("code.hex", "w");
        repeat (4) @(posedge clk);
        #1 rst = 0;
        for (k = 0; k < n + 40; k = k + 1) begin
            raw_a = (k < n) ? mem_a[k] : 6'h00;
            raw_b = (k < n) ? mem_b[k] : 6'h00;
            raw_c = (k < n) ? mem_c[k] : 6'h00;
            vv    = (k < n) ? mem_v[k] : 3'b111;
            @(posedge clk);
            #1 $fwrite(f, "%02x\n", code);
        end
        $fclose(f);
        $finish;
    end
endmodule
