// C5VRX by Twotoz and the C5VRX contributors: channel link testbench. A
// short press, a hold and another short press on the button model; the UART
// line is decoded here and every received byte printed for run_link_tb.py.
`timescale 1ns/1ps
module tb_channel_link;
    reg clk = 0, btn_n = 1;
    always #18.5 clk = ~clk;                // 27 MHz
    wire [5:0] index; wire valid, tx;
    channel_buttons #(.CLK_HZ(27000000)) ub (.clk(clk), .btn_n(btn_n), .index(index), .valid(valid));
    channel_link_tx #(.CLK_HZ(27000000)) ut (.clk(clk), .valid(valid), .index(index), .tx(tx));
    // UART receiver model, 115200 8N1
    real bit_ns = 1e9 / 115200.0;
    integer k; reg [7:0] b;
    initial begin
        forever begin
            @(negedge tx);
            #(bit_ns * 1.5);
            for (k = 0; k < 8; k = k + 1) begin b[k] = tx; #(bit_ns); end
            if (tx !== 1'b1) $display("FRAMING");
            $display("BYTE %02x", b);
        end
    end
    initial begin
        #(50_000_000);                      // 50 ms idle: nothing may be sent
        btn_n = 0; #(100_000_000); btn_n = 1;         // short press: R2 (index 1)
        #(60_000_000);
        btn_n = 0; #(800_000_000); btn_n = 1;         // hold: next band, A2 (index 9)
        #(60_000_000);
        $finish;
    end
endmodule
