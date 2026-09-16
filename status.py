# launcher_threaded.py
import threading
import subprocess
import time
import os
from datetime import datetime

def now_ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

def stream_output(label, process, logfile, prefix_timestamp=False, to_terminal=False):
    """Lê stdout do processo de forma robusta (readline), lida com \r e grava no logfile."""
    with open(logfile, "a", encoding="utf-8") as f:
        try:
            # iter(readline, '') lê até EOF mesmo que a linha não termine com '\n' imediatamente
            while True:
                line = process.stdout.readline()
                if line == "" and process.poll() is not None:
                    # EOF e processo terminado
                    break
                if line == "":
                    # nenhuma nova linha por enquanto; dar chance para processar outras threads
                    time.sleep(0.01)
                    continue
                # Normaliza retornos de carro (progresso em \r) -> transformar em linhas separadas
                line = line.rstrip("\n")
                # Substitui eventuais '\r' por '\n' e processa cada sub-linha
                parts = line.split("\r")
                for part in parts:
                    part = part.rstrip("\n")
                    if prefix_timestamp:
                        stamped = f"[{now_ts()}] [{label}] {part}"
                    else:
                        stamped = f"[{label}] {part}"
                    f.write(stamped + "\n")
                    f.flush()
                    if to_terminal:
                        print(stamped)
        finally:
            rc = process.wait()
            with open(logfile, "a", encoding="utf-8") as f_end:
                f_end.write(f"[launcher] Processo {label} finalizou com código {rc}\n")

def run_bot(filename, label, prefix_timestamp=False, to_terminal=False):
    logfile = f"{label}.log"
    # Forçar buffer unbuffered no ambiente do subprocesso (além do -u)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    process = subprocess.Popen(
        ["python", "-u", filename],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env
    )
    stream_output(label, process, logfile, prefix_timestamp=prefix_timestamp, to_terminal=to_terminal)

if __name__ == "__main__":
    # Thread para z.py (timestamp prefix)
    t1 = threading.Thread(target=run_bot, args=("z.py", "z"), kwargs={"prefix_timestamp": True, "to_terminal": False}, daemon=False)
    t1.start()

    time.sleep(60)  # manter seu delay

    # Thread para zcotacao.py (sem timestamp do launcher)
    t2 = threading.Thread(target=run_bot, args=("zcotacao.py", "zcotacao"), kwargs={"prefix_timestamp": False, "to_terminal": False}, daemon=False)
    t2.start()

    t1.join()
    t2.join()