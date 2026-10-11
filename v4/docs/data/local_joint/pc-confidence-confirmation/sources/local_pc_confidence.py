"""C5VRX/Twotoz context-dependent confidence encoder experiment.

GPL-3.0-only. Reuses the proved two-bank PC schedule, retaining32 phase
states and32 tokens. Last published DAC band chooses an origin confidence
radius. This tests whether sync robustness can change without globally
discarding active-video phase resolution. No training or firmware promotion.
"""
import numpy as np
import bs_model as BS
import local_joint_observation as J
import local_pc_prediction as P

decode = P.decode


def build(low_radius, high_radius):
    original = np.asarray(J.baseline()['lut'], np.int64)
    lut = original & 0x7ff
    for bank in range(4):
        radius = high_radius if bank % 2 else low_radius
        for raw in range(256):
            i, q = raw >> 4, raw & 15
            z = complex((i-16 if i>=8 else i)+.5, (q-16 if q>=8 else q)+.5)
            if low_radius == high_radius == .8:
                token = int(original[(raw>>4)|((raw&15)<<4)] >> 11)
            elif abs(z) < radius:
                token = 28+int(np.floor((np.angle(z)+np.pi)*4/(2*np.pi)))%4
            else:
                token = int(np.floor((np.angle(z)+np.pi)*28/(2*np.pi)))%28
            lut[bank*256+raw] |= token << 11
    model = J.baseline()
    model.update(name=f'PC-CONFIDENCE-r{low_radius:g}-{high_radius:g}', lut=lut.tolist())
    model.pop('formula', None)
    model['research'] = dict(mode_memory='instruction_path', low_radius=low_radius,
        high_radius=high_radius, switch='previous_published_DAC_MSB',
        physical_acceptance=False, hypothesis='frequency_band_conditioned_confidence_quantization')
    return model


def source(model):
    return '# Confidence banks; historical PC schedule, no predictive rotation.\n'+P.source(model)


def candidates():
    return [build(a,b) for a,b in ((.8,.8),(1.6,.8),(2.6,.8),(3.6,.8),
                                 (.8,1.6),(1.6,1.6))]


def verify():
    raw = np.random.default_rng(8900190).integers(0,256,8192,dtype=np.uint8)
    for model in candidates():
        stats = {}
        actual = np.asarray(BS.simulate(source(model),raw,len(raw),stats=stats,wrap_rom=True))&63
        expected = P.codes(raw,np.asarray(model['lut'],np.uint16))
        assert np.array_equal(actual,expected), model['name']
        assert all((pc%2)==(index%2) for index,pc in enumerate(stats['trace']))
        assert stats['bundles']==len(raw)-1
        if model['research']['low_radius']==model['research']['high_radius']==.8:
            assert np.array_equal(actual,J.O.model_codes(raw,J.baseline()))
    return dict(models=6,bytes_per_stream=8192,slots=8,lut_bytes=2048,
                alternating_controller_worker=True,zero_change_byte_exact_U85Q=True,
                scope='source_dataflow_not_physical_C5_timing_or_quality')


if __name__=='__main__':
    import json
    print(json.dumps(verify()))
