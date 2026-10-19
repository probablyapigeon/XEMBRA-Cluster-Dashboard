"""One bounded learning generation; the supervisor handles scheduling and recovery."""
import json
from pathlib import Path
from learned_compression import run_benchmark

if __name__=='__main__':
    report=run_benchmark(Path.cwd())
    print(json.dumps({'generation':report['generation'],'test':report['test'],'model_bytes':report['model_bytes']},indent=2))
