#!/usr/bin/env python3
"""
Organism ledger — SQLite.

Single source of truth for the organism's economic state.
- Every API cost is recorded as a negative entry.
- Every completed/paid gig is recorded as a positive entry.
- "Death" = balance <= 0 for `death_threshold_runs` consecutive runs.

Convention: everything in USD (cents), integer.
"""
import sqlite3, json, sys
from datetime import datetime, timezone
from pathlib import Path

DB = Path(__file__).parent / "organism.db"

# Survival policy (tunable)
POLICY = {
    "starting_capital_cents": 1000,      # $10.00 initial endowment
    "death_threshold_runs": 1,           # die the first time balance hits <= 0
    "starvation_threshold_cents": 200,   # below $2 = survival mode (cheap model, only high-EV tasks)
    "reproduction_threshold_cents": 5000 # above $50 sustained for N runs => spawn child (out of scope for v0)
}

def conn():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS ledger(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        kind TEXT NOT NULL,           -- 'cost' | 'revenue' | 'endowment' | 'adjustment'
        description TEXT NOT NULL,
        amount_cents INTEGER NOT NULL,-- signed
        meta TEXT                     -- json blob
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS runs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        balance_cents INTEGER NOT NULL,
        state TEXT NOT NULL,          -- 'alive' | 'survival' | 'dead'
        note TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS state_kv(
        k TEXT PRIMARY KEY, v TEXT NOT NULL
    )""")
    c.commit()
    return c

def kv_get(c,k,default=None):
    row=c.execute("SELECT v FROM state_kv WHERE k=?", (k,)).fetchone()
    return json.loads(row[0]) if row else default

def kv_set(c,k,v):
    c.execute("INSERT INTO state_kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
              (k, json.dumps(v)))

def bootstrap(c):
    if kv_get(c,"bootstrap_done"): return
    amt=POLICY["starting_capital_cents"]
    now=datetime.now(timezone.utc).isoformat()
    c.execute("INSERT INTO ledger(ts,kind,description,amount_cents) VALUES(?,?,?,?)",
              (now,"endowment","initial endowment",amt))
    kv_set(c,"bootstrap_done",True)
    kv_set(c,"birth_ts",now)
    kv_set(c,"alive",True)
    c.commit()

def balance(c):
    r=c.execute("SELECT COALESCE(SUM(amount_cents),0) FROM ledger").fetchone()
    return r[0]

def record_cost(c, cents, desc, meta=None):
    c.execute("INSERT INTO ledger(ts,kind,description,amount_cents,meta) VALUES(?,?,?,?,?)",
              (datetime.now(timezone.utc).isoformat(),"cost",desc,-abs(cents),
               json.dumps(meta) if meta else None))
    c.commit()

def record_revenue(c, cents, desc, meta=None):
    c.execute("INSERT INTO ledger(ts,kind,description,amount_cents,meta) VALUES(?,?,?,?,?)",
              (datetime.now(timezone.utc).isoformat(),"revenue",desc,abs(cents),
               json.dumps(meta) if meta else None))
    c.commit()

def recent_burn_cents(c, limit=20):
    rows=c.execute("SELECT amount_cents FROM ledger WHERE kind='cost' ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return -sum(r[0] for r in rows)

def get_state(c):
    bal=balance(c)
    alive=kv_get(c,"alive",True)
    if not alive:
        return "dead", bal
    if bal <= 0:
        return "dead", bal
    if bal < POLICY["starvation_threshold_cents"]:
        return "survival", bal
    return "alive", bal

def mark_dead(c, reason):
    kv_set(c,"alive",False)
    c.execute("INSERT INTO runs(ts,balance_cents,state,note) VALUES(?,?,?,?)",
              (datetime.now(timezone.utc).isoformat(), balance(c), "dead", reason))
    kv_set(c,"death_ts", datetime.now(timezone.utc).isoformat())
    kv_set(c,"death_reason", reason)
    c.commit()

def summary(c):
    bal=balance(c); state,_=get_state(c)
    return {
        "balance_cents": bal,
        "balance_usd": bal/100,
        "state": state,
        "birth": kv_get(c,"birth_ts"),
        "alive": kv_get(c,"alive",True),
        "recent_burn_cents": recent_burn_cents(c),
        "policy": POLICY,
    }

if __name__ == "__main__":
    c=conn(); bootstrap(c)
    cmd = sys.argv[1] if len(sys.argv)>1 else "summary"
    if cmd == "summary":
        print(json.dumps(summary(c), indent=2))
    elif cmd == "ledger":
        for r in c.execute("SELECT ts,kind,amount_cents,description FROM ledger ORDER BY id DESC LIMIT 30"):
            print(r)
    elif cmd == "cost":
        cents=int(sys.argv[2]); desc=" ".join(sys.argv[3:]) or "api call"
        record_cost(c,cents,desc); print(json.dumps(summary(c)))
    elif cmd == "revenue":
        cents=int(sys.argv[2]); desc=" ".join(sys.argv[3:]) or "income"
        record_revenue(c,cents,desc); print(json.dumps(summary(c)))
    elif cmd == "kill":
        mark_dead(c,"manual kill"); print(json.dumps(summary(c)))
    else:
        print("unknown command", file=sys.stderr); sys.exit(2)
