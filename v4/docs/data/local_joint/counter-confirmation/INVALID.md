# Invalid accelerated-model run; not candidate evidence

The independent assembly emulator rejected the initial counter implementation:
the counter compiler uses encoder bank zero, while the first accelerated model
and constructed encoder assumed bank three. Results in this directory must not
be used for model selection or improvement claims. The source fingerprint in
protocol.json records the rejected version. A corrected run belongs in a new
directory. Preserve this mismatch rather than treating simulation as hardware
proof. Discovered before any candidate promotion, build, push or flash.
