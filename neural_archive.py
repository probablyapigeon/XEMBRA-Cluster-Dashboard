"""Experimental CPU-only arithmetic coding benchmark with external model weights."""
import bisect
import hashlib
import json
import lzma
from pathlib import Path
import struct
import time
import zlib

import torch
from spinor_rnn import SpinorRNN, ConventionalRNN, corpus

HEADER = struct.Struct('!4sII32s')
TOP, HALF, QUARTER = (1 << 32) - 1, 1 << 31, 1 << 30


def cumulative(logits):
    # Positive integer frequencies avoid impossible symbols and floating-point coding.
    frequencies = (torch.softmax(logits.double(), -1) * 65280).floor().to(torch.int64) + 1
    return [0] + frequencies.cumsum(0).tolist()


class Predictor:
    def __init__(self, model):
        self.model = model.eval()
        self.state = None

    @torch.no_grad()
    def consume(self, byte):
        token = torch.tensor([[byte]])
        if isinstance(self.model, SpinorRNN):
            from spinor_rnn import rotation
            if self.state is None:
                self.state = torch.zeros(1, self.model.channels, 2, dtype=torch.complex64)
                self.state[..., 0] = 1
            operator = rotation(self.model.angles(token).reshape(1, self.model.channels, 3))
            self.state = (operator @ self.state.unsqueeze(-1)).squeeze(-1)
            features = torch.view_as_real(self.state).reshape(1, -1)
            logits = self.model.readout(features)[0]
        else:
            output, self.state = self.model.rnn(self.model.embedding(token), self.state)
            logits = self.model.readout(output)[0, 0]
        return cumulative(logits)


def encode(data, model):
    if len(data) > 65536:
        raise ValueError('Experimental archive limit is 65536 bytes')
    low, high, pending = 0, TOP, 0
    output = []

    def emit(bit):
        nonlocal pending
        output.append(bit)
        output.extend([1-bit] * pending)
        pending = 0

    predictor = Predictor(model)
    table = list(range(257))  # First byte has no previous context.
    for symbol in data:
        width = high - low + 1
        high = low + width * table[symbol+1] // table[-1] - 1
        low += width * table[symbol] // table[-1]
        while True:
            if high < HALF:
                emit(0)
            elif low >= HALF:
                emit(1)
                low -= HALF
                high -= HALF
            elif low >= QUARTER and high < 3*QUARTER:
                pending += 1
                low -= QUARTER
                high -= QUARTER
            else:
                break
            low, high = low*2, high*2+1
        table = predictor.consume(symbol)
    pending += 1
    emit(0 if low < QUARTER else 1)
    output.extend([0] * ((-len(output)) % 8))
    payload = bytes(sum(output[i+j] << (7-j) for j in range(8)) for i in range(0, len(output), 8))
    return HEADER.pack(b'NAC1', len(data), zlib.crc32(data), model_digest(model)) + payload


def model_digest(model):
    digest = hashlib.sha256()
    for name, tensor in model.state_dict().items():
        digest.update(name.encode())
        digest.update(tensor.detach().cpu().numpy().tobytes())
    return digest.digest()


def decode(archive, model):
    if len(archive) < HEADER.size + 1:
        raise ValueError('Truncated archive')
    magic, length, checksum, fingerprint = HEADER.unpack(archive[:HEADER.size])
    if magic != b'NAC1' or length > 65536 or fingerprint != model_digest(model):
        raise ValueError('Invalid archive or wrong model')
    payload = archive[HEADER.size:]
    position = 0

    def read():
        nonlocal position
        bit = (payload[position//8] >> (7-position%8)) & 1 if position < len(payload)*8 else 0
        position += 1
        return bit

    low, high, code = 0, TOP, 0
    for _ in range(32):
        code = code*2 + read()
    output = bytearray()
    predictor = Predictor(model)
    table = list(range(257))
    for _ in range(length):
        width = high - low + 1
        value = ((code-low+1)*table[-1]-1)//width
        symbol = bisect.bisect_right(table, value)-1
        if not 0 <= symbol <= 255:
            raise ValueError('Invalid coded symbol')
        output.append(symbol)
        high = low + width*table[symbol+1]//table[-1]-1
        low += width*table[symbol]//table[-1]
        while True:
            if high < HALF:
                pass
            elif low >= HALF:
                low -= HALF
                high -= HALF
                code -= HALF
            elif low >= QUARTER and high < 3*QUARTER:
                low -= QUARTER
                high -= QUARTER
                code -= QUARTER
            else:
                break
            low, high, code = low*2, high*2+1, code*2+read()
        table = predictor.consume(symbol)
    result = bytes(output)
    if zlib.crc32(result) != checksum:
        raise ValueError('Checksum mismatch')
    # Reject trailing garbage, truncated termination and noncanonical bitstreams.
    if encode(result, model) != archive:
        raise ValueError('Noncanonical or damaged archive')
    return result


def benchmark(directory=Path('logs/spinor-demo')):
    torch.set_num_threads(1)
    models = {'spinor': SpinorRNN(), 'gru': ConventionalRNN()}
    for name, model in models.items():
        model.load_state_dict(torch.load(directory / (name+'_weights.pt'), weights_only=True))
    excluded = {bytes(row.tolist()) for seed, count in ((11, 384), (22, 128), (33, 128))
                for row in corpus(seed, count)}
    unseen = list(dict.fromkeys(bytes(row.tolist()) for row in corpus(9876, 128)
                               if bytes(row.tolist()) not in excluded))[:32]
    if len(unseen) != 32:
        raise ValueError('Insufficient held-out records')
    datasets = {'synthetic_unseen': unseen,
                'real_python_samples': [Path(name).read_bytes()[:256] for name in
                                        ('shared_evolution.py', 'learning_worker.py', 'xembra_cluster.py', 'evolution_engine.py')]}
    results = {'header_bytes_per_record': HEADER.size,
               'limits': 'Small bounded CPU benchmark. External weights required. No portable cross-platform decoding guarantee. '
                         'Real Python samples were not training data; synthetic data shares training patterns.',
               'datasets': {}}
    for dataset, records in datasets.items():
        row = {'records': len(records), 'input_bytes': sum(map(len, records)), 'codecs': {}}
        row['raw_with_equal_framing_bytes'] = row['input_bytes'] + HEADER.size*len(records)
        for name, model in models.items():
            start = time.perf_counter()
            archives = [encode(record, model) for record in records]
            exact = sum(decode(archive, model) == record for archive, record in zip(archives, records))
            size = sum(map(len, archives))
            weights = (directory / (name+'_weights.pt')).stat().st_size
            row['codecs'][name] = {'archive_bytes': size, 'model_bytes': weights,
                                   'total_with_model_bytes': size+weights, 'exact_roundtrips': exact,
                                   'encode_and_verified_decode_seconds': time.perf_counter()-start}
        # Same 44-byte framing allowance for standard codecs; model cost is zero.
        for name, compressor, decompressor in [('zlib', zlib.compress, zlib.decompress),
                                               ('lzma', lzma.compress, lzma.decompress)]:
            payloads = [compressor(record) for record in records]
            row['codecs'][name] = {'archive_bytes': sum(map(len, payloads))+HEADER.size*len(records),
                                   'model_bytes': 0,
                                   'exact_roundtrips': sum(decompressor(p) == r for p, r in zip(payloads, records))}
        # A practical baseline can exploit repetition across records in one archive.
        bundle = b''.join(struct.pack('!I', len(record))+record for record in records)
        packed = zlib.compress(bundle, 9)
        if zlib.decompress(packed) != bundle:
            raise AssertionError('Bundle reconstruction failed')
        row['zlib_single_bundle_with_header_bytes'] = len(packed)+HEADER.size
        results['datasets'][dataset] = row
    (directory / 'compression_metrics.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    benchmark()
