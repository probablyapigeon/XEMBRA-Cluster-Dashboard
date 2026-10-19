# Measured learning and lossless compression

The learning workload replaces truncation/noisy QT scores with an explicit
supervised task: predict which of raw storage, zlib, and LZMA will make a file
smallest. A NumPy neural network has 10 byte-statistic inputs, 24 tanh hidden
neurons, and three softmax outputs: **339 trainable weights and biases**.
Adam/backpropagation changes those weights using measured training-file codec
sizes. It learns a codec-selection policy, not a new entropy coder or general
intelligence. No language-model API or paid training service is involved.

## See the results

```powershell
py -3.14 learned_compression.py benchmark --state logs/learning-demo
```

The first generation uses 180 training files, 60 validation files, and 60 test
files from separate deterministic synthetic generators. Data ranges from random
bytes to repeated blocks, structured logs, word sequences, sparse data, and
UTF-8 text. Training changes weights using only training labels; checkpoint
selection uses validation bytes and validation loss. Test files are scored only
after selecting the model, and test scores do not decide promotion. File hashes
and split IDs are recorded. Persistent models are loaded on subsequent runs.

On the initial local benchmark, the trained model chose the smallest codec for
98.3% of held-out files, compared with 73.3% for the untrained model. It produced
86,692 bytes from 275,342 input bytes, versus 87,480 bytes for fixed zlib with a
raw-data fallback (about **0.9% smaller**). All 60 files reconstructed exactly.
These are measured synthetic results, not promises for other datasets. Most of
the 68.5% saving versus original data comes from established codecs; the neural
selector's extra improvement versus the stronger zlib baseline is much smaller.

`learning_metrics.json` records accuracy, cross-entropy, actual archive sizes,
untrained and fixed-codec baselines, oracle size, exact round trips, and a learning
curve. `selector_model.json` stores weights separately. All archive headers and
checksums are counted in compression comparisons. Model storage is reported
separately; archives contain a codec tag, so decoding does not require the model.
The encoder falls back to raw storage if its chosen codec expands a file.

## Try your own data

```powershell
py -3.14 learned_compression.py benchmark --corpus C:\MyTestFiles --state logs/my-corpus
```

Use at least 20 distinct nonempty files. The benchmark samples the first 64 KiB
of up to 256 files, deduplicates by content hash, and makes disjoint file-level
70/15/15 splits. Inputs stay local and are never overwritten or uploaded. A
changed corpus requires a fresh state folder to avoid leaking previous training
data into a new test split. Results may be worse than fixed compressors.

Compress and reconstruct a complete file (up to 64 MiB):

```powershell
py -3.14 learned_compression.py compress input.txt output.xcb --model logs/learning-demo/selector_model.json
py -3.14 learned_compression.py decompress output.xcb restored.txt
```

Output files must not already exist. The decoder verifies original length and
CRC32, rejects incomplete/trailing compressed data, and bounds decoded output.
CRC32 detects accidental corruption; it is not authentication or encryption.

## Dashboard and workers

Docker defaults to `XEMBRA_WORKLOAD=learning`. Each scheduled worker trains one
bounded generation, saves its weights, and reports held-out results under its
own persistent `/data/worker-N` directory. The dashboard's **Learning & lossless
compression** section shows trained/untrained accuracy, sizes against fixed
codecs and oracle, exact reconstruction counts, and train/validation loss curves.
The original numeric/QT experiment remains available with
`XEMBRA_WORKLOAD=legacy`; neural learning does not use its noisy fitness score.
The network mode's existing compression-spec protocol does not yet exchange
these neural model weights.

```powershell
$env:XEMBRA_WORKLOAD = 'learning'
docker compose up -d --build
```

Repeated benchmark runs are not repeated independent discoveries; test splits
remain fixed for comparison. Real task intelligence beyond codec selection,
universally better compression, and cross-worker neural-model consensus have
not been demonstrated.

## Verification

```powershell
py -3.14 -m unittest discover -s tests -p test_learned_compression.py -v
```

Checks cover exact reconstruction of binary/Unicode/empty input, corruption and
output limits, numerical gradient agreement with backpropagation, saved weights,
unseen-file evaluation, continued training, and file-level split deduplication.

References: [Python zlib](https://docs.python.org/3.14/library/zlib.html) and
[train/test leakage guidance](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage).
