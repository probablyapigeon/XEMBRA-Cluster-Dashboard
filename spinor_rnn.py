"""Bounded CPU experiment: SU(2) recurrent byte prediction, not an archive codec."""
import argparse
import copy
import json
import math
from pathlib import Path

import torch
from torch import nn


def rotation(angles):
    """exp(-i theta.sigma), using the three Pauli generators."""
    radius = torch.linalg.vector_norm(angles, dim=-1)
    scale = torch.sinc(radius / math.pi)
    x, y, z = (angles * scale.unsqueeze(-1)).unbind(-1)
    c = torch.cos(radius)
    return torch.stack((torch.stack((c - 1j*z, -y - 1j*x), -1),
                        torch.stack((y - 1j*x, c + 1j*z), -1)), -2)


class SpinorRNN(nn.Module):
    def __init__(self, channels=12):
        super().__init__()
        self.channels = channels
        self.angles = nn.Embedding(256, channels * 3)
        nn.init.normal_(self.angles.weight, std=0.15)
        self.readout = nn.Linear(channels * 4, 256)

    def forward(self, tokens):
        # Each channel is a two-component complex spinor. State resets per record.
        state = torch.zeros(tokens.shape[0], self.channels, 2,
                            dtype=torch.complex64, device=tokens.device)
        state[..., 0] = 1
        operators = rotation(self.angles(tokens).reshape(*tokens.shape, self.channels, 3))
        outputs = []
        for step in range(tokens.shape[1]):
            state = (operators[:, step] @ state.unsqueeze(-1)).squeeze(-1)
            features = torch.view_as_real(state).reshape(tokens.shape[0], -1)
            outputs.append(self.readout(features))
        return torch.stack(outputs, 1)


class ConventionalRNN(nn.Module):
    def __init__(self):
        super().__init__()
        self.embedding = nn.Embedding(256, 12)
        self.rnn = nn.GRU(12, 12, batch_first=True)
        self.readout = nn.Linear(12, 256)

    def forward(self, tokens):
        states, _ = self.rnn(self.embedding(tokens))
        return self.readout(states)


def corpus(seed, count, length=33):
    """Independent records from a deliberately simple, shared pattern distribution."""
    generator = torch.Generator().manual_seed(seed)
    motifs = [b'abcde', b'01234567', b'hello world\n', b'ABBA', b'xyzXYZ']
    records = []
    for _ in range(count):
        motif = motifs[int(torch.randint(len(motifs), (), generator=generator))]
        offset = int(torch.randint(len(motif), (), generator=generator))
        record = torch.tensor([motif[(i+offset) % len(motif)] for i in range(length)])
        noise = torch.rand(length, generator=generator) < 0.08
        record[noise] = torch.randint(256, (int(noise.sum()),), generator=generator)
        records.append(record)
    return torch.stack(records)


def bits(model, records):
    with torch.no_grad():
        logits = model(records[:, :-1])
        return float(nn.functional.cross_entropy(logits.reshape(-1, 256),
                     records[:, 1:].reshape(-1))) / math.log(2)


def train(model, train_data, validation, epochs):
    optimizer = torch.optim.Adam(model.parameters(), lr=0.015)
    best = float('inf')
    checkpoint = copy.deepcopy(model.state_dict())
    curve = []
    for epoch in range(epochs):
        optimizer.zero_grad()
        logits = model(train_data[:, :-1])
        loss = nn.functional.cross_entropy(logits.reshape(-1, 256), train_data[:, 1:].reshape(-1))
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        score = bits(model, validation)
        curve.append({'epoch': epoch + 1, 'train_bits_per_byte': float(loss.detach())/math.log(2),
                      'validation_bits_per_byte': score})
        if score < best:
            best, checkpoint = score, copy.deepcopy(model.state_dict())
    model.load_state_dict(checkpoint)
    return curve


def benchmark(output, epochs=60):
    torch.set_num_threads(1)
    torch.manual_seed(1234)
    seen = set()
    datasets = []
    for seed, count in ((11, 96), (22, 32), (33, 32)):
        unique = []
        for record in corpus(seed, count * 4):
            key = tuple(record.tolist())
            if key not in seen:
                seen.add(key)
                unique.append(record)
                if len(unique) == count:
                    break
        if len(unique) != count:
            raise ValueError('Insufficient distinct benchmark records')
        datasets.append(torch.stack(unique))
    train_data, validation, test = datasets
    splits = [set(map(tuple, data.tolist())) for data in (train_data, validation, test)]
    if any(splits[i] & splits[j] for i, j in ((0, 1), (0, 2), (1, 2))):
        raise ValueError('Overlapping benchmark records')
    counts = torch.bincount(train_data[:, 1:].flatten(), minlength=256).float() + 1
    probabilities = counts / counts.sum()
    result = {'dataset': 'synthetic noisy repeated motifs; disjoint records, shared patterns',
              'epochs': epochs, 'test_predictions': test[:, 1:].numel(),
              'uniform_bits_per_byte': 8.0,
              'training_unigram_bits_per_byte': float(-torch.log2(probabilities[test[:, 1:]]).mean()),
              'models': {},
              'limitations': 'Prediction cross-entropy only, not compressed archive size. No entropy coder. '
                             'Model storage and runtime excluded. Synthetic results do not establish real-file gains.'}
    output.mkdir(parents=True, exist_ok=True)
    for name, model in [('spinor', SpinorRNN()), ('gru', ConventionalRNN())]:
        initial = bits(model, test)
        curve = train(model, train_data, validation, epochs)
        model_path = output / (name + '_weights.pt')
        torch.save(model.state_dict(), model_path)
        result['models'][name] = {'parameters': sum(p.numel() for p in model.parameters()),
                                 'untrained_test_bits_per_byte': initial,
                                 'trained_test_bits_per_byte': bits(model, test),
                                 'model_file_bytes': model_path.stat().st_size,
                                 'curve': curve}
    (output / 'metrics.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('logs/spinor-demo'))
    parser.add_argument('--epochs', type=int, default=60)
    args = parser.parse_args()
    if not 1 <= args.epochs <= 500:
        parser.error('--epochs must be between 1 and 500')
    report = benchmark(args.output, args.epochs)
    for model_name, metrics in report['models'].items():
        print(f"{model_name}: {metrics['untrained_test_bits_per_byte']:.3f} -> "
              f"{metrics['trained_test_bits_per_byte']:.3f} test bits/byte", flush=True)
    print(f"Unigram: {report['training_unigram_bits_per_byte']:.3f} bits/byte; report: {args.output / 'metrics.json'}")
