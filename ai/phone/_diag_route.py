"""진단: 전화를 걸었을 때 S26+ 통화 소리가 PC(블루투스 핸즈프리)로 넘어오는지 1초마다 기록하고 15초 뒤 끊는다."""
import subprocess
import sys
import time

import sounddevice as sd

import call_agent as ca

num = sys.argv[1]


def endpoints():
    ps = ("Get-PnpDevice -Class AudioEndpoint | Where-Object { $_.FriendlyName -match 'S26' } | "
          "ForEach-Object { $_.Status + ' ' + $_.FriendlyName }")
    return subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                          text=True, encoding="utf-8", errors="replace").stdout.strip().replace("\n", " | ")


def pa_list():
    sd._terminate(); sd._initialize()
    apis = sd.query_hostapis()
    return [f"{i}:{apis[d['hostapi']]['name']}:{'in' if d['max_input_channels'] else 'out'}"
            for i, d in enumerate(sd.query_devices()) if "Hands-Free" in d["name"] or "S26" in d["name"]]


print("걸기 전:", ca.call_state(), endpoints(), pa_list(), flush=True)
ca.dial(num)
for k in range(15):
    time.sleep(1)
    print(f"+{k+1}s 상태={ca.call_state()} 연결={ca.answered()} 장치={endpoints()} PA={pa_list()}", flush=True)
ca.hangup()
time.sleep(2)
print("끊은 뒤:", ca.call_state(), flush=True)
print(ca.adb("shell", "dumpsys", "telecom")[:0] or "", end="")
