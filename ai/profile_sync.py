# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET profile 동기화 — 호윤의 '진짜 메모리'(클로드 메모리 디렉토리)를
#   COMET 의 profile/ 로 복사해 코멧이 호윤을 '최신으로' 알게 한다.
#   (profile/MEMORY.md 색인이 매 턴 SYSTEM 에 로드됨 → 갱신되면 코멧 인식도 갱신.)
#
#  기본 = 복사·갱신만(안전): 소스의 .md 를 profile/ 로 덮어쓰고, 새 파일은 추가.
#         소스에서 사라진 항목(profile 에만 남은 stale)은 '보고만' 하고 안 지운다.
#  prune=True = 미러: stale 도 삭제(소스와 완전 일치). '프로필 동기화 정리'.
#  의존성 0(os/shutil). 데몬은 갱신 후 재시작해야 SYSTEM 이 새 profile 로 다시 로드됨.
# ═══════════════════════════════════════════════════════════════
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
DST = os.path.join(HERE, "profile")
# 소스 = 클로드(나)의 메모리 디렉토리 = 호윤 메모리의 단일 진실
SRC = r"C:\Users\USER\.claude\projects\C--Users-USER\memory"


def _read(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except Exception:
        return None


def sync(prune=False, src=SRC, dst=DST):
    """소스 .md → profile/ 복사·갱신. 결과 요약 문자열 반환."""
    if not os.path.isdir(src):
        return f"동기화 실패: 소스 메모리 폴더를 못 찾음 ({src})"
    os.makedirs(dst, exist_ok=True)
    src_files = {f for f in os.listdir(src) if f.endswith(".md")}
    dst_files = {f for f in os.listdir(dst) if f.endswith(".md")}
    if not src_files:
        return f"동기화 보류: 소스에 .md 가 없음 ({src})"

    added, updated, unchanged = [], [], []
    for f in sorted(src_files):
        s, d = os.path.join(src, f), os.path.join(dst, f)
        sb = _read(s)
        if sb is None:
            continue
        if f not in dst_files:
            shutil.copy2(s, d); added.append(f)
        elif _read(d) != sb:
            shutil.copy2(s, d); updated.append(f)
        else:
            unchanged.append(f)

    stale = sorted(dst_files - src_files)          # profile 에만 있고 소스엔 없는 것
    pruned = []
    if prune:
        for f in stale:
            try:
                os.remove(os.path.join(dst, f)); pruned.append(f)
            except Exception:
                pass

    lines = [f"프로필 동기화 완료 — 추가 {len(added)} · 갱신 {len(updated)} · 그대로 {len(unchanged)}"]
    if added:
        lines.append("  + 추가: " + ", ".join(a[:-3] for a in added))
    if updated:
        lines.append("  ~ 갱신: " + ", ".join(u[:-3] for u in updated))
    if prune and pruned:
        lines.append("  - 삭제(정리): " + ", ".join(p[:-3] for p in pruned))
    elif stale:
        lines.append(f"  ⚠️ 소스에서 사라진 {len(stale)}개는 그대로 둠(미러하려면 '프로필 동기화 정리'): "
                     + ", ".join(s[:-3] for s in stale))
    lines.append("  ※ 데몬은 재시작해야 새 프로필이 SYSTEM에 다시 로드됨.")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    print(sync(prune="--prune" in sys.argv))
