# Spinor recurrent experiment

This is a separate, bounded CPU experiment inside XEMBRA. It does not change
the running dashboard, daemon, existing compression selector, or Docker image.

Run with Python and PyTorch installed (PyTorch is an optional experiment dependency):

```powershell
py -3.14 spinor_rnn.py
py -3.14 -m unittest discover -s tests -p test_spinor_rnn.py
```

Twelve two-component complex states start at (1, 0). Each byte selects learned
three-axis rotation angles. Pauli generators define U = exp(-i theta.sigma),
an SU(2) operator applied to each state. Different byte operators can fail to
commute, so input order matters. A trained linear readout predicts the next
byte from real and imaginary state components. Autograd trains rotations and
readout through the sequence. There is no quantum hardware or quantum advantage.

The benchmark compares the spinor model with a conventional GRU trained on the
same data, an untrained model, uniform prediction, and a training-only unigram
baseline. They have different parameter counts, reported explicitly. Independent
synthetic records share a deliberately simple pattern distribution; they are
not a realistic general compression corpus. Validation selects checkpoints;
the held-out test split does not select models. State resets at record boundaries.

Results and weight files are saved under logs/spinor-demo. Repeating the command
replaces those experiment artifacts. Prediction cross-entropy in bits per byte
is an ideal coding-rate measure, NOT an actual archive size. This experiment
does not itself include an entropy coder. The separate coding experiment below
adds one and tests small source samples; portable decoding remains unverified.
Weights and runtime cost must be counted before claiming practical compression.

## Actual lossless coding experiment

```powershell
py -3.14 neural_archive.py
py -3.14 -m unittest discover -s tests -p test_neural_archive.py
```

After running the training command above, this uses its saved weights to drive
a 32-bit integer arithmetic coder. The decoder independently predicts from
already reconstructed bytes. Archives contain length, CRC32, and a SHA-256
model fingerprint (44 header bytes). Decoding requires the exact external
weights and matching CPU numerical behavior; this is not yet a portable archive
format. Wrong weights, oversized lengths, damage and noncanonical streams are
rejected. Inputs are capped at 64 KiB; this benchmark uses much smaller samples.

The report logs actual neural archive bytes, model bytes separately and added,
exact round trips, and encode/decode time. Standard codec comparisons include
an equal 44-byte per-record framing allowance, plus a stronger zlib baseline
that bundles records together with length prefixes. Results are saved to
logs/spinor-demo/compression_metrics.json. No source input is modified.

The initial test used 32 unseen noisy motif records and four 256-byte source
samples. Spinor archives used 1,827 bytes on 1,056 synthetic input bytes versus
2,190 for independently zlib-coded records with equal framing. However, the
spinor weights added 89,312 bytes. On 1,024 bytes of real source samples,
spinor archives were 1,271 bytes versus zlib's 898, even excluding weights.
All 36 records reconstructed exactly for each neural model. These small tests
do not establish a practical compression advantage.

Related research: [Unitary Evolution Recurrent Neural Networks](https://proceedings.mlr.press/v48/arjovsky16.html).
This implementation is an experimental SU(2) design, not a reproduction of that paper.
