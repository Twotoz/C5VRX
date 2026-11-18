// C5VRX by Twotoz and the C5VRX contributors: TANG9K_DUAL align_b testbench.
// Two free-running clocks (C5 A 25.000 ns, C5 B +period_ps offset), each
// streaming its own file (stim_a.hex / stim_b.hex). Writes, per clk_a
// cycle, the aligned pair and status to align.txt for run_align_tb.py.
`timescale 1ps/1ps
module tb_align_b;
    reg clk_a = 0, clk_b = 0, rst = 1;
    reg [7:0] mem_a [0:400000];
    reg [7:0] mem_b [0:400000];
    reg [7:0] raw_a = 0, raw_b_in = 0;
    wire [7:0] a_al, b_al;
    wire en_b, b_alive;
    wire [5:0] lag;
    integer n, pb, ia = 0, ib = 0, f, b_start;
    align_b dut (.clk_b(clk_b), .raw_b_in(raw_b_in), .clk_a(clk_a), .rst_a(rst), .raw_a(raw_a),
                 .raw_a_d(a_al), .raw_b(b_al), .en_b(en_b), .b_alive(b_alive), .lag(lag));
    initial begin
        if (!$value$plusargs("n=%d", n)) n = 1000;
        if (!$value$plusargs("pb=%d", pb)) pb = 25000;
        if (!$value$plusargs("b_start=%d", b_start)) b_start = 7000;
        $readmemh("stim_a.hex", mem_a);
        $readmemh("stim_b.hex", mem_b);
    end
    always #12500 clk_a = ~clk_a;
    initial begin
        #(b_start);
        forever #(pb / 2) clk_b = ~clk_b;
    end
    always @(posedge clk_b) begin
        raw_b_in <= mem_b[ib];
        ib <= ib + 1;
    end
    initial begin
        f = $fopen("align.txt", "w");
        repeat (4) @(posedge clk_a);
        rst <= 0;
        while (ia < n) begin
            @(posedge clk_a);
            raw_a <= mem_a[ia];
            ia = ia + 1;
            $fwrite(f, "%0d %02x %02x %0d %0d %0d\n", ia, a_al, b_al, en_b, b_alive, lag);
        end
        $fclose(f);
        $finish;
    end
endmodule
