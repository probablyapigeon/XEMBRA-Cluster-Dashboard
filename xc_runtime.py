#!/usr/bin/env python3
"""XC Runtime v0.3.0

Compiler frontend + deterministic interpreter for the executable core of the
XEMBRA Core language (XC).

Pipeline:
    .xc source -> tokenizer -> parser -> JSON-serializable XEMBRA IR -> runtime

Runtime v0.3 includes v0.2 and adds:
    - named observations and event handlers
    - persistent episodic memory stores
    - full agent checkpoint save/load (state + action + memory + event counter)
    - salience/similarity/recency-ranked episodic retrieval
    - recall_vector() and recall_signal()
    - checkpoint resume equivalence controls
    - deterministic event replay

Scientific boundary:
    XC executes declared mathematical dynamics. Semantic names such as trust,
    fear, Focus, or memory are model labels, not evidence that the runtime is a
    literal biological or psychological model.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import argparse
import hashlib
import json
import math
import sys
from typing import Any

RUNTIME_VERSION = "0.3.0"
IR_VERSION = "0.3"
MEMORY_FORMAT_VERSION = "0.2"
CHECKPOINT_FORMAT_VERSION = "0.1"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class XCError(Exception):
    pass

class XCLexError(XCError):
    pass

class XCParseError(XCError):
    pass

class XCValidationError(XCError):
    pass

class XCRuntimeError(XCError):
    pass


# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    line: int
    col: int

SINGLE = set("{}()[],:;=+-*/@<>")
TWO = {"<-", "<=", ">=", "==", "!="}


def tokenize(text: str) -> list[Token]:
    out: list[Token] = []
    i = 0
    line = 1
    col = 1
    n = len(text)

    def advance_char(ch: str) -> None:
        nonlocal line, col
        if ch == "\n":
            line += 1
            col = 1
        else:
            col += 1

    while i < n:
        ch = text[i]
        if ch.isspace():
            advance_char(ch); i += 1; continue

        if text.startswith("//", i):
            while i < n and text[i] != "\n":
                advance_char(text[i]); i += 1
            continue

        start_line, start_col = line, col
        pair = text[i:i+2]
        if pair in TWO:
            out.append(Token("SYM", pair, start_line, start_col))
            advance_char(text[i]); advance_char(text[i+1]); i += 2; continue

        if ch in SINGLE:
            out.append(Token("SYM", ch, start_line, start_col))
            advance_char(ch); i += 1; continue

        if ch == '"':
            i += 1; advance_char('"')
            chars = []
            while i < n:
                c = text[i]
                if c == '"':
                    advance_char(c); i += 1
                    break
                if c == "\\":
                    if i + 1 >= n:
                        raise XCLexError(f"Unterminated escape at {line}:{col}")
                    nxt = text[i+1]
                    mapping = {"n":"\n", "t":"\t", "r":"\r", '"':'"', "\\":"\\"}
                    if nxt not in mapping:
                        raise XCLexError(f"Unsupported escape \\{nxt} at {line}:{col}")
                    chars.append(mapping[nxt])
                    advance_char(c); advance_char(nxt); i += 2
                    continue
                chars.append(c); advance_char(c); i += 1
            else:
                raise XCLexError(f"Unterminated string at {start_line}:{start_col}")
            out.append(Token("STRING", "".join(chars), start_line, start_col))
            continue

        if ch.isdigit() or (ch == "." and i + 1 < n and text[i+1].isdigit()):
            j = i; has_dot = False
            while j < n and (text[j].isdigit() or text[j] == "."):
                if text[j] == ".":
                    if has_dot: break
                    has_dot = True
                j += 1
            if j < n and text[j] in "eE":
                k = j + 1
                if k < n and text[k] in "+-": k += 1
                if k >= n or not text[k].isdigit():
                    raise XCLexError(f"Malformed exponent at {start_line}:{start_col}")
                while k < n and text[k].isdigit(): k += 1
                j = k
            val = text[i:j]
            out.append(Token("NUMBER", val, start_line, start_col))
            for c in text[i:j]: advance_char(c)
            i = j; continue

        if ch.isalpha() or ch == "_":
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] == "_"): j += 1
            val = text[i:j]
            out.append(Token("IDENT", val, start_line, start_col))
            for c in text[i:j]: advance_char(c)
            i = j; continue

        raise XCLexError(f"Unexpected character {ch!r} at {line}:{col}")

    out.append(Token("EOF", "", line, col))
    return out


# ---------------------------------------------------------------------------
# Parser -> JSON serializable IR
# ---------------------------------------------------------------------------

PRECEDENCE = {
    "==":1, "!=":1, "<":1, ">":1, "<=":1, ">=":1,
    "+":2, "-":2, "*":3, "/":3, "@":4,
}

class Parser:
    def __init__(self, tokens: list[Token]):
        self.t = tokens
        self.i = 0

    @property
    def cur(self) -> Token:
        return self.t[self.i]

    def at(self, value: str) -> bool:
        return self.cur.value == value

    def accept(self, value: str) -> bool:
        if self.at(value): self.i += 1; return True
        return False

    def expect(self, value: str) -> Token:
        tok = self.cur
        if tok.value != value:
            raise XCParseError(f"Expected {value!r} at {tok.line}:{tok.col}, got {tok.value!r}")
        self.i += 1
        return tok

    def expect_kind(self, kind: str) -> Token:
        tok = self.cur
        if tok.kind != kind:
            raise XCParseError(f"Expected {kind} at {tok.line}:{tok.col}, got {tok.kind} {tok.value!r}")
        self.i += 1
        return tok

    def skip_semis(self):
        while self.accept(";"): pass

    def parse(self) -> dict[str, Any]:
        if self.cur.value != "xembra":
            raise XCParseError(f"Runtime v0.3 expects one top-level xembra block; got {self.cur.value!r}")
        p = self.parse_xembra()
        self.skip_semis()
        if self.cur.kind != "EOF":
            raise XCParseError(f"Unexpected top-level token {self.cur.value!r} at {self.cur.line}:{self.cur.col}")
        return {"ir_version":IR_VERSION, "runtime_min_version":"0.3.0", "program":p}

    def parse_xembra(self):
        self.expect("xembra")
        name = self.expect_kind("IDENT").value
        version = None
        if self.accept("version"): version = self.expect_kind("NUMBER").value
        self.expect("{")
        p = {
            "name":name, "version":version, "seed":None,
            "states":{}, "memories":{}, "matrices":{}, "operators":{},
            "measures":{}, "actions":[], "policies":{}, "cycle":[],
            "observations":{}, "events":{},
        }
        while not self.accept("}"):
            self.skip_semis()
            if self.at("}"): self.i += 1; break
            kw = self.cur.value
            if kw == "seed":
                self.expect("seed"); self.expect("=")
                tok = self.expect_kind("NUMBER")
                if "." in tok.value or "e" in tok.value.lower():
                    raise XCParseError(f"seed must be integer at {tok.line}:{tok.col}")
                p["seed"] = int(tok.value); self.skip_semis()
            elif kw == "state":
                n,d = self.parse_state(); p["states"][n] = d
            elif kw == "memory":
                n,d = self.parse_assignment_block("memory"); p["memories"][n] = d
            elif kw == "observation":
                n,d = self.parse_assignment_block("observation")
                if "vector" not in d: raise XCParseError(f"observation {n} requires vector = [...]")
                p["observations"][n] = d
            elif kw == "event":
                n,d = self.parse_event(); p["events"][n] = d
            elif kw == "matrix":
                self.expect("matrix"); n = self.expect_kind("IDENT").value; self.expect("=")
                p["matrices"][n] = self.parse_expression(); self.skip_semis()
            elif kw == "operator":
                n,d = self.parse_assignment_block("operator")
                if "map" not in d: raise XCParseError(f"operator {n} requires map = ...")
                p["operators"][n] = d
            elif kw == "measure":
                self.expect("measure"); n = self.expect_kind("IDENT").value
                self.expect("{"); self.expect("value"); self.expect("=")
                e = self.parse_expression(); self.skip_semis(); self.expect("}"); self.skip_semis()
                p["measures"][n] = e
            elif kw == "actions":
                self.expect("actions"); self.expect("{")
                while not self.accept("}"):
                    if self.accept(";") or self.accept(","): continue
                    p["actions"].append(self.expect_kind("IDENT").value)
                self.skip_semis()
            elif kw == "policy":
                n,d = self.parse_policy(); p["policies"][n] = d
            elif kw == "cycle":
                p["cycle"] = self.parse_cycle()
            elif kw in {"connection","lattice","experiment","constraint","import"}:
                tok = self.cur
                raise XCParseError(f"{kw} is not implemented by XC Runtime v0.3 (at {tok.line}:{tok.col})")
            else:
                tok = self.cur; raise XCParseError(f"Unknown component {kw!r} at {tok.line}:{tok.col}")
        return p

    def parse_state(self):
        self.expect("state"); name = self.expect_kind("IDENT").value; self.expect("{")
        fields=[]
        while not self.accept("}"):
            self.skip_semis()
            if self.at("}"): self.i += 1; break
            fname=self.expect_kind("IDENT").value; self.expect(":"); ftype=self.expect_kind("IDENT").value
            init={"kind":"number","value":0.0}
            if self.accept("="): init=self.parse_expression()
            fields.append({"name":fname,"type":ftype,"init":init}); self.skip_semis()
        self.skip_semis(); return name,{"fields":fields}

    def parse_assignment_block(self, keyword):
        self.expect(keyword); name=self.expect_kind("IDENT").value; self.expect("{"); data={}
        while not self.accept("}"):
            self.skip_semis()
            if self.at("}"): self.i += 1; break
            key=self.expect_kind("IDENT").value; self.expect("="); data[key]=self.parse_expression(); self.skip_semis()
        self.skip_semis(); return name,data

    def parse_policy(self):
        self.expect("policy"); name=self.expect_kind("IDENT").value; self.expect("{")
        scores=[]; select=None
        while not self.accept("}"):
            self.skip_semis()
            if self.at("}"): self.i += 1; break
            if self.accept("score"):
                action=self.expect_kind("IDENT").value; self.expect("="); e=self.parse_expression()
                scores.append({"action":action,"expr":e}); self.skip_semis()
            elif self.accept("select"):
                select=self.expect_kind("IDENT").value; self.skip_semis()
            else:
                tok=self.cur; raise XCParseError(f"Expected score/select in policy {name} at {tok.line}:{tok.col}")
        self.skip_semis(); return name,{"scores":scores,"select":select}

    def parse_cycle(self):
        self.expect("cycle"); self.expect("{"); updates=[]
        while not self.accept("}"):
            self.skip_semis()
            if self.at("}"): self.i += 1; break
            target=self.expect_kind("IDENT").value; self.expect("<-"); e=self.parse_expression()
            updates.append({"target":target,"expr":e}); self.skip_semis()
        self.skip_semis(); return updates

    def parse_event(self):
        self.expect("event"); name=self.expect_kind("IDENT").value; self.expect("{")
        stmts=[]
        while not self.accept("}"):
            self.skip_semis()
            if self.at("}"): self.i += 1; break
            if self.accept("observe"):
                obs=self.expect_kind("IDENT").value
                stmts.append({"kind":"observe","name":obs}); self.skip_semis(); continue
            if self.accept("remember"):
                mem=self.expect_kind("IDENT").value
                stmts.append({"kind":"remember","name":mem}); self.skip_semis(); continue
            if self.accept("cycle"):
                count=1
                if self.cur.kind == "NUMBER":
                    tok=self.expect_kind("NUMBER")
                    if "." in tok.value or "e" in tok.value.lower():
                        raise XCParseError(f"event cycle count must be integer at {tok.line}:{tok.col}")
                    count=int(tok.value)
                stmts.append({"kind":"cycle","count":count}); self.skip_semis(); continue
            target=self.expect_kind("IDENT").value; self.expect("<-"); e=self.parse_expression()
            stmts.append({"kind":"update","target":target,"expr":e}); self.skip_semis()
        self.skip_semis(); return name,{"statements":stmts}

    def parse_expression(self,min_prec=0):
        left=self.parse_unary()
        while self.cur.value in PRECEDENCE and PRECEDENCE[self.cur.value] >= min_prec:
            op=self.cur.value; prec=PRECEDENCE[op]; self.i += 1
            right=self.parse_expression(prec+1)
            left={"kind":"binary","op":op,"left":left,"right":right}
        return left

    def parse_unary(self):
        if self.accept("-"): return {"kind":"unary","op":"-","expr":self.parse_unary()}
        if self.accept("+"): return self.parse_unary()
        return self.parse_postfix()

    def parse_postfix(self):
        node=self.parse_primary()
        while self.accept("("):
            if node.get("kind") != "ident":
                tok=self.cur; raise XCParseError(f"Only named calls supported near {tok.line}:{tok.col}")
            args=[]
            if not self.accept(")"):
                while True:
                    args.append(self.parse_expression())
                    if self.accept(")"): break
                    self.expect(",")
            node={"kind":"call","name":node["name"],"args":args}
        return node

    def parse_primary(self):
        tok=self.cur
        if tok.kind == "NUMBER": self.i += 1; return {"kind":"number","value":float(tok.value)}
        if tok.kind == "STRING": self.i += 1; return {"kind":"string","value":tok.value}
        if tok.kind == "IDENT":
            self.i += 1
            if tok.value == "true": return {"kind":"bool","value":True}
            if tok.value == "false": return {"kind":"bool","value":False}
            return {"kind":"ident","name":tok.value}
        if self.accept("("):
            e=self.parse_expression(); self.expect(")"); return e
        if self.accept("["):
            items=[]
            if not self.accept("]"):
                while True:
                    items.append(self.parse_expression())
                    if self.accept("]"): break
                    self.expect(",")
            return {"kind":"list","items":items}
        raise XCParseError(f"Expected expression at {tok.line}:{tok.col}, got {tok.value!r}")


# ---------------------------------------------------------------------------
# Numeric operations
# ---------------------------------------------------------------------------

def is_seq(x): return isinstance(x,list)

def deep_map(x,f): return [deep_map(v,f) for v in x] if is_seq(x) else f(x)

def same_shape(a,b):
    if is_seq(a) != is_seq(b): return False
    if not is_seq(a): return True
    return len(a)==len(b) and all(same_shape(x,y) for x,y in zip(a,b))

def shape(x):
    if not is_seq(x): return ()
    if not x: return (0,)
    first=shape(x[0])
    for v in x[1:]:
        if shape(v)!=first: raise XCRuntimeError("Ragged list/matrix is not allowed")
    return (len(x),)+first

def elementwise(a,b,op,opname):
    if is_seq(a) and is_seq(b):
        if not same_shape(a,b): raise XCRuntimeError(f"Shape mismatch for {opname}: {shape(a)} vs {shape(b)}")
        return [elementwise(x,y,op,opname) for x,y in zip(a,b)]
    if is_seq(a): return [elementwise(x,b,op,opname) for x in a]
    if is_seq(b): return [elementwise(a,y,op,opname) for y in b]
    return op(float(a),float(b))

def matmul(a,b):
    sa,sb=shape(a),shape(b)
    if len(sa)==2 and len(sb)==1:
        r,c=sa
        if c!=sb[0]: raise XCRuntimeError(f"Matrix/vector @ mismatch: {sa} @ {sb}")
        return [sum(float(a[i][j])*float(b[j]) for j in range(c)) for i in range(r)]
    if len(sa)==1 and len(sb)==1:
        if sa[0]!=sb[0]: raise XCRuntimeError(f"Vector dot mismatch: {sa} @ {sb}")
        return sum(float(x)*float(y) for x,y in zip(a,b))
    if len(sa)==2 and len(sb)==2:
        r,c=sa; c2,k=sb
        if c!=c2: raise XCRuntimeError(f"Matrix/matrix @ mismatch: {sa} @ {sb}")
        return [[sum(float(a[i][j])*float(b[j][q]) for j in range(c)) for q in range(k)] for i in range(r)]
    raise XCRuntimeError(f"Unsupported @ operands with shapes {sa} and {sb}")

def flatten(x):
    if is_seq(x):
        for v in x: yield from flatten(v)
    else: yield float(x)

def jsonable(x):
    if isinstance(x,float):
        if not math.isfinite(x): raise XCRuntimeError("Non-finite numeric result")
        return x
    if isinstance(x,(int,bool,str)) or x is None: return x
    if isinstance(x,list): return [jsonable(v) for v in x]
    if isinstance(x,dict): return {str(k):jsonable(v) for k,v in x.items()}
    return x


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------

class Runtime:
    def __init__(self, ir: dict[str,Any], memory_data: dict[str,Any] | None=None, checkpoint_data: dict[str,Any] | None=None):
        self.ir=ir; self.p=ir["program"]
        self.state_name=None; self.field_names=[]; self.state=[]; self.matrices={}
        self.action=None
        self.memory_specs={}; self.memory_stores={}
        self.observations={}
        self.current_event=None; self.current_observation_name=None; self.current_observation=[]
        self.event_counter=0
        self._initialize()
        if memory_data is not None and checkpoint_data is not None:
            raise XCValidationError("Use either memory_data or checkpoint_data, not both")
        if checkpoint_data is not None:
            self.load_checkpoint_data(checkpoint_data)
        elif memory_data is not None:
            self.load_memory_data(memory_data)

    def _initialize(self):
        if len(self.p["states"])!=1: raise XCValidationError("XC Runtime v0.3 supports exactly one state block")
        self.state_name=next(iter(self.p["states"]))
        fields=self.p["states"][self.state_name]["fields"]
        self.field_names=[f["name"] for f in fields]
        if len(set(self.field_names))!=len(self.field_names): raise XCValidationError("Duplicate state field names")
        if not fields: raise XCValidationError("State must contain at least one field")
        self.state=[]
        for f in fields:
            v=self.eval_expr(f["init"],{})
            if is_seq(v): raise XCValidationError(f"State field {f['name']} must initialize to scalar")
            self.state.append(float(v))

        for n,e in self.p["matrices"].items():
            v=self.eval_expr(e,{})
            if len(shape(v))!=2: raise XCValidationError(f"matrix {n} must be rectangular 2D, got shape {shape(v)}")
            self.matrices[n]=v

        for n,a in self.p["memories"].items():
            spec={k:self.eval_expr(v,{}) for k,v in a.items()}
            capacity=int(spec.get("capacity",64)); decay=float(spec.get("decay",0.0))
            top_k=int(spec.get("top_k",4))
            similarity_weight=float(spec.get("similarity_weight",0.25))
            salience_weight=float(spec.get("salience_weight",0.50))
            recency_weight=float(spec.get("recency_weight",0.25))
            if capacity<1: raise XCValidationError(f"memory {n} capacity must be >=1")
            if not (0.0 <= decay < 1.0): raise XCValidationError(f"memory {n} decay must be in [0,1)")
            if top_k<1: raise XCValidationError(f"memory {n} top_k must be >=1")
            if any(w < 0 for w in (similarity_weight,salience_weight,recency_weight)):
                raise XCValidationError(f"memory {n} retrieval weights must be >=0")
            if similarity_weight+salience_weight+recency_weight <= 0:
                raise XCValidationError(f"memory {n} retrieval weights cannot all be zero")
            self.memory_specs[n]={
                "capacity":capacity,"decay":decay,"top_k":top_k,
                "similarity_weight":similarity_weight,
                "salience_weight":salience_weight,
                "recency_weight":recency_weight,
            }
            self.memory_stores[n]=[]

        for n,a in self.p["observations"].items():
            data={k:self.eval_expr(v,{}) for k,v in a.items()}
            vec=data["vector"]
            if shape(vec)!=(len(self.field_names),):
                raise XCValidationError(f"observation {n} vector shape {shape(vec)} does not match state {(len(self.field_names),)}")
            data["vector"]=[float(x) for x in vec]
            salience=float(data.get("salience",0.5))
            if not (0.0 <= salience <= 1.0):
                raise XCValidationError(f"observation {n} salience must be in [0,1]")
            data["salience"]=salience
            self.observations[n]=data

        self.current_observation=[0.0]*len(self.field_names)
        self.validate()

    def state_env(self,vector=None):
        vec=self.state if vector is None else vector
        if shape(vec)!=(len(self.field_names),): raise XCRuntimeError(f"State vector has shape {shape(vec)}; expected {(len(self.field_names),)}")
        env={self.state_name:list(map(float,vec))}
        env.update(self.matrices)
        env.update({name:float(vec[i]) for i,name in enumerate(self.field_names)})
        env["Obs"]=list(self.current_observation)
        env["event_index"]=float(self.event_counter)
        env["pi"]=math.pi; env["e"]=math.e
        if self.action is not None: env["action"]=self.action
        return env

    def validate(self):
        if len(set(self.p["actions"]))!=len(self.p["actions"]): raise XCValidationError("Duplicate action names")
        action_set=set(self.p["actions"])
        for pname,pol in self.p["policies"].items():
            if pol.get("select")!="argmax": raise XCValidationError(f"policy {pname}: Runtime v0.3 supports only select argmax")
            if not pol["scores"]: raise XCValidationError(f"policy {pname} has no scores")
            for s in pol["scores"]:
                if s["action"] not in action_set: raise XCValidationError(f"policy {pname} scores undeclared action {s['action']}")

        for oname in self.p["operators"]:
            out=self.call_operator(oname,list(self.state))
            if shape(out)!=(len(self.field_names),): raise XCValidationError(f"operator {oname} output shape {shape(out)} does not match state")

        env=self.state_env()
        for m,e in self.p["measures"].items():
            try: self.eval_expr(e,env)
            except XCError as exc: raise XCValidationError(f"measure {m}: {exc}") from exc

        allowed={self.state_name,"action"}
        for u in self.p["cycle"]:
            if u["target"] not in allowed: raise XCValidationError(f"cycle target {u['target']!r} unsupported")

        for ename,e in self.p["events"].items():
            seen_observe=False
            for s in e["statements"]:
                if s["kind"]=="observe":
                    seen_observe=True
                    if s["name"] not in self.observations: raise XCValidationError(f"event {ename} observes undeclared observation {s['name']}")
                elif s["kind"]=="remember":
                    if s["name"] not in self.memory_specs: raise XCValidationError(f"event {ename} remembers into undeclared memory {s['name']}")
                elif s["kind"]=="cycle":
                    if s["count"]<0: raise XCValidationError(f"event {ename} cycle count must be >=0")
                elif s["kind"]=="update":
                    if s["target"] not in allowed: raise XCValidationError(f"event {ename} update target {s['target']!r} unsupported")
            if not seen_observe: raise XCValidationError(f"event {ename} must contain observe NAME")

    def call_operator(self,name,vector):
        if name not in self.p["operators"]: raise XCRuntimeError(f"Unknown operator {name}")
        if shape(vector)!=(len(self.field_names),): raise XCRuntimeError(f"operator {name} expected state vector")
        return self.eval_expr(self.p["operators"][name]["map"],self.state_env(vector))

    def call_policy(self,name,vector):
        if name not in self.p["policies"]: raise XCRuntimeError(f"Unknown policy {name}")
        env=self.state_env(vector); scored=[]
        for item in self.p["policies"][name]["scores"]:
            v=self.eval_expr(item["expr"],env)
            if is_seq(v): raise XCRuntimeError(f"Policy score for {item['action']} must be scalar")
            scored.append((float(v),item["action"]))
        best=max(range(len(scored)),key=lambda i:scored[i][0])
        return scored[best][1]

    def _memory_name_arg(self,args,func):
        if len(args)<1 or not isinstance(args[0],str): raise XCRuntimeError(f"{func} first argument must be memory name string")
        name=args[0]
        if name not in self.memory_specs: raise XCRuntimeError(f"Unknown memory {name}")
        return name

    def memory_vector(self,name):
        spec=self.memory_specs[name]; entries=self.memory_stores[name]
        d=len(self.field_names)
        if not entries: return [0.0]*d
        total=0.0; acc=[0.0]*d
        for e in entries:
            age=max(0,self.event_counter-int(e["event_index"]))
            w=(1.0-spec["decay"])**age
            total += w
            for i,x in enumerate(e["vector"]): acc[i] += w*float(x)
        if total<=0: return [0.0]*d
        return [x/total for x in acc]

    def memory_signal(self,name,label):
        spec=self.memory_specs[name]; entries=self.memory_stores[name]
        if not entries: return 0.0
        total=0.0; hit=0.0
        for e in entries:
            age=max(0,self.event_counter-int(e["event_index"]))
            w=(1.0-spec["decay"])**age; total+=w
            if e.get("event")==label or e.get("observation")==label: hit+=w
        return 0.0 if total<=0 else hit/total

    @staticmethod
    def _cosine_similarity(a,b):
        if shape(a)!=shape(b) or len(shape(a))!=1:
            raise XCRuntimeError("cosine similarity requires equal vectors")
        dot=sum(float(x)*float(y) for x,y in zip(a,b))
        na=math.sqrt(sum(float(x)*float(x) for x in a))
        nb=math.sqrt(sum(float(y)*float(y) for y in b))
        if na<=1e-15 or nb<=1e-15: return 0.0
        return max(0.0,min(1.0,dot/(na*nb)))

    def retrieve_memories(self,name,query=None,top_k=None):
        """Rank memories by similarity + salience + recency.

        This is deterministic heuristic retrieval, not learned human memory.
        """
        spec=self.memory_specs[name]; entries=self.memory_stores[name]
        if not entries: return []
        q=list(self.current_observation if query is None else query)
        if shape(q)!=(len(self.field_names),):
            raise XCRuntimeError(f"retrieval query shape {shape(q)} does not match state")
        ws=spec["similarity_weight"]; wa=spec["salience_weight"]; wr=spec["recency_weight"]
        denom=ws+wa+wr
        ranked=[]
        for pos,e in enumerate(entries):
            age=max(0,self.event_counter-int(e["event_index"]))
            similarity=self._cosine_similarity(q,e["vector"])
            salience=float(e.get("salience",0.5))
            recency=(1.0-spec["decay"])**age
            score=(ws*similarity + wa*salience + wr*recency)/denom
            ranked.append({
                "score":float(score),"similarity":float(similarity),
                "salience":salience,"recency":float(recency),
                "entry":e,"position":pos,
            })
        # Stable deterministic tie break: newer event, then later store position.
        ranked.sort(key=lambda r:(r["score"],int(r["entry"].get("event_index",-1)),r["position"]),reverse=True)
        k=spec["top_k"] if top_k is None else int(top_k)
        if k<1: raise XCRuntimeError("top_k must be >=1")
        return ranked[:k]

    def recall_vector(self,name):
        ranked=self.retrieve_memories(name)
        d=len(self.field_names)
        if not ranked: return [0.0]*d
        total=sum(max(0.0,r["score"]) for r in ranked)
        if total<=1e-15: return [0.0]*d
        out=[0.0]*d
        for r in ranked:
            w=max(0.0,r["score"])
            for i,x in enumerate(r["entry"]["vector"]): out[i]+=w*float(x)
        return [x/total for x in out]

    def recall_signal(self,name,label):
        ranked=self.retrieve_memories(name)
        if not ranked: return 0.0
        total=sum(max(0.0,r["score"]) for r in ranked)
        if total<=1e-15: return 0.0
        hit=sum(max(0.0,r["score"]) for r in ranked
                if r["entry"].get("event")==label or r["entry"].get("observation")==label)
        return hit/total

    def eval_expr(self,node,env):
        k=node["kind"]
        if k=="number": return float(node["value"])
        if k=="string": return str(node["value"])
        if k=="bool": return bool(node["value"])
        if k=="ident":
            n=node["name"]
            if n in env: return env[n]
            if n in self.matrices: return self.matrices[n]
            raise XCRuntimeError(f"Undefined identifier {n}")
        if k=="list": return [self.eval_expr(x,env) for x in node["items"]]
        if k=="unary":
            v=self.eval_expr(node["expr"],env)
            if node["op"]=="-": return deep_map(v,lambda x:-float(x))
            raise XCRuntimeError(f"Unknown unary operator {node['op']}")
        if k=="binary":
            a=self.eval_expr(node["left"],env); b=self.eval_expr(node["right"],env); op=node["op"]
            if op=="+": return elementwise(a,b,lambda x,y:x+y,"+")
            if op=="-": return elementwise(a,b,lambda x,y:x-y,"-")
            if op=="*": return elementwise(a,b,lambda x,y:x*y,"*")
            if op=="/": return elementwise(a,b,lambda x,y:x/y,"/")
            if op=="@": return matmul(a,b)
            if is_seq(a) or is_seq(b): raise XCRuntimeError(f"Comparison {op} requires scalars")
            return {"==":a==b,"!=":a!=b,"<":a<b,">":a>b,"<=":a<=b,">=":a>=b}[op]
        if k=="call":
            name=node["name"]
            args=[self.eval_expr(a,env) for a in node["args"]]
            if name in self.p["operators"]:
                self._arity(name,args,1); return self.call_operator(name,args[0])
            if name in self.p["policies"]:
                self._arity(name,args,1); return self.call_policy(name,args[0])
            if name=="tanh": self._arity(name,args,1); return deep_map(args[0],lambda x:math.tanh(float(x)))
            if name=="norm": self._arity(name,args,1); return math.sqrt(sum(x*x for x in flatten(args[0])))
            if name=="sigmoid":
                self._arity(name,args,1)
                def sig(x):
                    x=float(x)
                    if x>=0: z=math.exp(-x); return 1/(1+z)
                    z=math.exp(x); return z/(1+z)
                return deep_map(args[0],sig)
            if name=="clamp":
                self._arity(name,args,3); lo,hi=float(args[1]),float(args[2])
                return deep_map(args[0],lambda x:min(hi,max(lo,float(x))))
            if name=="memory_vector":
                self._arity(name,args,1); mem=self._memory_name_arg(args,name); return self.memory_vector(mem)
            if name=="memory_count":
                self._arity(name,args,1); mem=self._memory_name_arg(args,name); return float(len(self.memory_stores[mem]))
            if name=="memory_signal":
                self._arity(name,args,2); mem=self._memory_name_arg(args,name)
                if not isinstance(args[1],str): raise XCRuntimeError("memory_signal second argument must be string label")
                return self.memory_signal(mem,args[1])
            if name=="recall_vector":
                self._arity(name,args,1); mem=self._memory_name_arg(args,name); return self.recall_vector(mem)
            if name=="recall_signal":
                self._arity(name,args,2); mem=self._memory_name_arg(args,name)
                if not isinstance(args[1],str): raise XCRuntimeError("recall_signal second argument must be string label")
                return self.recall_signal(mem,args[1])
            raise XCRuntimeError(f"Unknown function/operator/policy {name}")
        raise XCRuntimeError(f"Unknown expression node kind {k}")

    @staticmethod
    def _arity(name,args,n):
        if len(args)!=n: raise XCRuntimeError(f"{name} expects {n} arguments, got {len(args)}")

    def measures(self):
        env=self.state_env(); return {n:jsonable(self.eval_expr(e,env)) for n,e in self.p["measures"].items()}

    def state_dict(self): return {n:float(self.state[i]) for i,n in enumerate(self.field_names)}

    def memory_summary(self):
        return {n:{
            "count":len(self.memory_stores[n]),
            "vector":self.memory_vector(n),
            "recall_vector":self.recall_vector(n),
        } for n in self.memory_specs}

    def snapshot(self,step):
        return {"step":step,"state":self.state_dict(),"action":self.action,"measures":self.measures(),"memory":self.memory_summary()}

    def _apply_update(self,target,value):
        if target==self.state_name:
            if shape(value)!=(len(self.field_names),): raise XCRuntimeError(f"State update returned shape {shape(value)}")
            self.state=[float(x) for x in value]
        elif target=="action":
            if not isinstance(value,str): raise XCRuntimeError("action update must evaluate to action identifier")
            if value not in self.p["actions"]: raise XCRuntimeError(f"Selected undeclared action {value}")
            self.action=value

    def step(self):
        for u in self.p["cycle"]:
            v=self.eval_expr(u["expr"],self.state_env()); self._apply_update(u["target"],v)

    def remember(self,name,state_before_event):
        if self.current_observation_name is None: raise XCRuntimeError("remember requires an active observation")
        entry={
            "event_index":self.event_counter,
            "event":self.current_event,
            "observation":self.current_observation_name,
            "vector":list(self.current_observation),
            "salience":float(self.observations[self.current_observation_name].get("salience",0.5)),
            "state_before_event":dict(state_before_event),
            "state_at_remember":self.state_dict(),
            "action_at_remember":self.action,
        }
        store=self.memory_stores[name]; store.append(entry)
        cap=self.memory_specs[name]["capacity"]
        if len(store)>cap: del store[:-cap]

    def dispatch(self,event_name):
        if event_name not in self.p["events"]: raise XCRuntimeError(f"Unknown event {event_name}")
        self.current_event=event_name; self.current_observation_name=None; self.current_observation=[0.0]*len(self.field_names)
        state_before=self.state_dict(); cycles_run=0
        for s in self.p["events"][event_name]["statements"]:
            if s["kind"]=="observe":
                self.current_observation_name=s["name"]; self.current_observation=list(self.observations[s["name"]]["vector"])
            elif s["kind"]=="update":
                v=self.eval_expr(s["expr"],self.state_env()); self._apply_update(s["target"],v)
            elif s["kind"]=="remember":
                self.remember(s["name"],state_before)
            elif s["kind"]=="cycle":
                for _ in range(s["count"]): self.step(); cycles_run += 1
        snap={
            "event_index":self.event_counter,
            "event":event_name,
            "observation":self.current_observation_name,
            "observation_vector":list(self.current_observation),
            "cycles_run":cycles_run,
            "state_before":state_before,
            "state_after":self.state_dict(),
            "action":self.action,
            "measures":self.measures(),
            "memory":self.memory_summary(),
        }
        self.event_counter += 1
        return snap

    def run(self,cycles):
        if cycles<0: raise XCValidationError("cycles must be >=0")
        trace=[self.snapshot(0)]
        for i in range(1,cycles+1): self.step(); trace.append(self.snapshot(i))
        return {
            "runtime_version":RUNTIME_VERSION,"ir_version":self.ir.get("ir_version"),
            "program":self.p["name"],"program_version":self.p.get("version"),"seed":self.p.get("seed"),
            "state_name":self.state_name,"state_fields":self.field_names,"cycles":cycles,"trace":trace,
            "memory":self.export_memory(),
        }

    def run_events(self,events):
        trace=[]
        for name in events: trace.append(self.dispatch(name))
        return {
            "runtime_version":RUNTIME_VERSION,"ir_version":self.ir.get("ir_version"),
            "program":self.p["name"],"program_version":self.p.get("version"),"seed":self.p.get("seed"),
            "state_name":self.state_name,"state_fields":self.field_names,
            "events":list(events),"trace":trace,"final_state":self.state_dict(),"final_action":self.action,
            "memory":self.export_memory(),
        }

    def export_memory(self):
        return {
            "memory_format_version":MEMORY_FORMAT_VERSION,
            "program":self.p["name"],
            "event_counter":self.event_counter,
            "stores":jsonable(self.memory_stores),
        }

    def load_memory_data(self,data):
        if data.get("program") not in {None,self.p["name"]}: raise XCValidationError("Memory file belongs to different program")
        stores=data.get("stores",{})
        for name,entries in stores.items():
            if name not in self.memory_specs: raise XCValidationError(f"Memory file contains unknown store {name}")
            checked=[]
            for e in entries:
                vec=e.get("vector")
                if shape(vec)!=(len(self.field_names),): raise XCValidationError(f"Memory entry in {name} has invalid vector shape")
                checked.append(dict(e))
            cap=self.memory_specs[name]["capacity"]
            self.memory_stores[name]=checked[-cap:]
        self.event_counter=int(data.get("event_counter",0))

    def export_checkpoint(self):
        return {
            "checkpoint_format_version":CHECKPOINT_FORMAT_VERSION,
            "runtime_version":RUNTIME_VERSION,
            "ir_version":self.ir.get("ir_version"),
            "program":self.p["name"],
            "program_version":self.p.get("version"),
            "source_sha256":self.ir.get("source",{}).get("sha256"),
            "seed":self.p.get("seed"),
            "state_name":self.state_name,
            "state_fields":list(self.field_names),
            "state":list(self.state),
            "action":self.action,
            "event_counter":self.event_counter,
            "memory":self.export_memory(),
            "rng_state":None,
            "rng_note":"Runtime v0.3 has no stochastic primitives; seed is retained for forward compatibility.",
        }

    def load_checkpoint_data(self,data):
        if data.get("program") != self.p["name"]:
            raise XCValidationError("Checkpoint belongs to different program")
        expected_hash=self.ir.get("source",{}).get("sha256")
        got_hash=data.get("source_sha256")
        if expected_hash and got_hash and expected_hash != got_hash:
            raise XCValidationError("Checkpoint source hash does not match this XC program")
        if data.get("state_name") != self.state_name:
            raise XCValidationError("Checkpoint state name mismatch")
        if data.get("state_fields") != self.field_names:
            raise XCValidationError("Checkpoint state fields mismatch")
        vec=data.get("state")
        if shape(vec)!=(len(self.field_names),):
            raise XCValidationError("Checkpoint state vector shape mismatch")
        self.state=[float(x) for x in vec]
        action=data.get("action")
        if action is not None and action not in self.p["actions"]:
            raise XCValidationError(f"Checkpoint contains undeclared action {action}")
        self.action=action
        mem=data.get("memory",{})
        self.load_memory_data(mem)
        self.event_counter=int(data.get("event_counter",mem.get("event_counter",0)))
        if self.event_counter < 0: raise XCValidationError("Checkpoint event_counter must be >=0")
        # Checkpoints are defined between events; active observation is reset.
        self.current_event=None
        self.current_observation_name=None
        self.current_observation=[0.0]*len(self.field_names)


# ---------------------------------------------------------------------------
# Compiler / IO
# ---------------------------------------------------------------------------

def compile_text(text,source_name="<memory>"):
    ir=Parser(tokenize(text)).parse()
    ir["source"]={"name":source_name,"sha256":hashlib.sha256(text.encode("utf-8")).hexdigest()}
    Runtime(ir)
    return ir

def compile_file(path:Path): return compile_text(path.read_text(encoding="utf-8"),str(path))

def trace_hash(result):
    payload=json.dumps(result["trace"],sort_keys=True,separators=(",",":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def load_memory_file(path): return json.loads(Path(path).read_text(encoding="utf-8"))
def load_checkpoint_file(path): return json.loads(Path(path).read_text(encoding="utf-8"))

def save_json(path,obj): Path(path).write_text(json.dumps(obj,indent=2),encoding="utf-8")

def read_events(args):
    events=[]
    if getattr(args,"events",None): events.extend(args.events)
    ef=getattr(args,"events_file",None)
    if ef:
        text=Path(ef).read_text(encoding="utf-8").strip()
        if text.startswith("["):
            data=json.loads(text)
            if not isinstance(data,list) or not all(isinstance(x,str) for x in data): raise XCValidationError("events JSON must be a list of strings")
            events.extend(data)
        else:
            events.extend([line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")])
    if not events: raise XCValidationError("No events supplied; use --events or --events-file")
    return events

def print_run(result):
    print(f"XC Runtime {result['runtime_version']} — {result['program']} v{result['program_version']}")
    print(f"cycles={result['cycles']} seed={result['seed']}"); print("-")
    fields=result["state_fields"]
    for snap in result["trace"]:
        state=" ".join(f"{k}={snap['state'][k]:.6f}" for k in fields)
        meas=" ".join(f"{k}={v:.6f}" if isinstance(v,(int,float)) else f"{k}={v}" for k,v in snap["measures"].items())
        action=snap["action"] if snap["action"] is not None else "-"
        mem=" ".join(f"{n}:{d['count']}" for n,d in snap.get("memory",{}).items())
        print(f"step={snap['step']:>2} action={action:<14} {state} {meas} memory=[{mem}]")
    print("-"); print("trace_sha256="+trace_hash(result))

def print_event_run(result):
    print(f"XC Runtime {result['runtime_version']} — {result['program']} v{result['program_version']}")
    print(f"events={len(result['events'])} seed={result['seed']}"); print("-")
    for s in result["trace"]:
        st=" ".join(f"{k}={v:.4f}" for k,v in s["state_after"].items())
        mem=" ".join(f"{n}:{d['count']}" for n,d in s["memory"].items())
        print(f"event={s['event_index']:>2} {s['event']:<12} obs={str(s['observation']):<12} action={str(s['action']):<12} {st} memory=[{mem}]")
    print("-")
    print("final_action="+str(result["final_action"]))
    print("trace_sha256="+trace_hash(result))

def make_runtime(ir,args):
    mem=None; checkpoint=None
    if getattr(args,"memory_in",None): mem=load_memory_file(args.memory_in)
    if getattr(args,"checkpoint_in",None): checkpoint=load_checkpoint_file(args.checkpoint_in)
    if mem is not None and checkpoint is not None:
        raise XCValidationError("Use either --memory-in or --checkpoint-in, not both")
    return Runtime(ir,memory_data=mem,checkpoint_data=checkpoint)

def maybe_outputs(rt,result,args):
    if getattr(args,"trace",None): save_json(args.trace,result); print(f"trace written: {args.trace}")
    if getattr(args,"memory_out",None): save_json(args.memory_out,rt.export_memory()); print(f"memory written: {args.memory_out}")
    if getattr(args,"checkpoint_out",None): save_json(args.checkpoint_out,rt.export_checkpoint()); print(f"checkpoint written: {args.checkpoint_out}")


def cmd_check(args):
    ir=compile_file(Path(args.source)); p=ir["program"]; rt=Runtime(ir)
    print("XC CHECK PASS")
    print(f"program:      {p['name']} v{p.get('version')}")
    print(f"runtime:      {RUNTIME_VERSION}")
    print(f"state:        {rt.state_name}[{len(rt.field_names)}] = {', '.join(rt.field_names)}")
    print(f"memories:     {', '.join(p['memories']) or '(none)'}")
    print(f"observations: {', '.join(p['observations']) or '(none)'}")
    print(f"events:       {', '.join(p['events']) or '(none)'}")
    print(f"operators:    {', '.join(p['operators']) or '(none)'}")
    print(f"policies:     {', '.join(p['policies']) or '(none)'}")
    print(f"actions:      {', '.join(p['actions']) or '(none)'}")
    print(f"cycle updates:{len(p['cycle'])}")
    print(f"source_sha256:{ir['source']['sha256']}")

def cmd_compile(args):
    ir=compile_file(Path(args.source)); out=Path(args.output) if args.output else Path(args.source).with_suffix(".xcir.json")
    save_json(out,ir); print(f"XC COMPILE PASS -> {out}"); print(f"source_sha256: {ir['source']['sha256']}")

def cmd_run(args):
    ir=compile_file(Path(args.source)); rt=make_runtime(ir,args); result=rt.run(args.cycles); print_run(result); maybe_outputs(rt,result,args)

def cmd_run_ir(args):
    ir=json.loads(Path(args.ir).read_text(encoding="utf-8")); rt=make_runtime(ir,args); result=rt.run(args.cycles); print_run(result); maybe_outputs(rt,result,args)

def cmd_run_events(args):
    ir=compile_file(Path(args.source)); events=read_events(args); rt=make_runtime(ir,args); result=rt.run_events(events); print_event_run(result); maybe_outputs(rt,result,args)

def cmd_run_events_ir(args):
    ir=json.loads(Path(args.ir).read_text(encoding="utf-8")); events=read_events(args); rt=make_runtime(ir,args); result=rt.run_events(events); print_event_run(result); maybe_outputs(rt,result,args)

def cmd_replay(args):
    ir=compile_file(Path(args.source)); a=Runtime(ir).run(args.cycles); b=Runtime(ir).run(args.cycles)
    if a["trace"]!=b["trace"] or trace_hash(a)!=trace_hash(b): raise XCRuntimeError("Deterministic replay FAILED")
    print("XC REPLAY PASS"); print(f"cycles: {args.cycles}"); print(f"trace_sha256: {trace_hash(a)}")

def cmd_replay_events(args):
    ir=compile_file(Path(args.source)); events=read_events(args)
    a=Runtime(ir).run_events(events); b=Runtime(ir).run_events(events)
    if a["trace"]!=b["trace"] or a["memory"]!=b["memory"] or trace_hash(a)!=trace_hash(b): raise XCRuntimeError("Deterministic event replay FAILED")
    print("XC EVENT REPLAY PASS"); print(f"events: {len(events)}"); print(f"trace_sha256: {trace_hash(a)}")

def cmd_memory(args):
    data=load_memory_file(args.memory_file)
    print("XC MEMORY")
    print(f"program: {data.get('program')}"); print(f"event_counter: {data.get('event_counter',0)}")
    for n,entries in data.get("stores",{}).items():
        print(f"{n}: {len(entries)} entries")
        for e in entries[-args.last:]: print(f"  [{e.get('event_index')}] {e.get('event')} / {e.get('observation')}")


def cmd_checkpoint(args):
    data=load_checkpoint_file(args.checkpoint_file)
    print("XC CHECKPOINT")
    print(f"program:       {data.get('program')} v{data.get('program_version')}")
    print(f"runtime:       {data.get('runtime_version')}")
    print(f"source_sha256: {data.get('source_sha256')}")
    print(f"event_counter: {data.get('event_counter',0)}")
    print(f"action:        {data.get('action')}")
    fields=data.get('state_fields',[]); state=data.get('state',[])
    print("state:         "+" ".join(f"{k}={float(v):.6f}" for k,v in zip(fields,state)))
    for n,entries in data.get('memory',{}).get('stores',{}).items():
        print(f"memory {n}:    {len(entries)} entries")

def cmd_recall(args):
    ir=compile_file(Path(args.source)); rt=make_runtime(ir,args)
    if args.observation not in rt.observations:
        raise XCValidationError(f"Unknown observation {args.observation}")
    if args.memory not in rt.memory_specs:
        raise XCValidationError(f"Unknown memory {args.memory}")
    rt.current_observation_name=args.observation
    rt.current_observation=list(rt.observations[args.observation]['vector'])
    ranked=rt.retrieve_memories(args.memory,top_k=args.top)
    print("XC RECALL")
    print(f"memory:      {args.memory}")
    print(f"observation: {args.observation}")
    print(f"entries:     {len(rt.memory_stores[args.memory])}")
    if not ranked:
        print("(no memories)"); return
    for i,r in enumerate(ranked):
        e=r['entry']
        print(f"[{i}] score={r['score']:.6f} sim={r['similarity']:.6f} salience={r['salience']:.3f} recency={r['recency']:.6f} event={e.get('event')} idx={e.get('event_index')}")
    print("recall_vector="+json.dumps(rt.recall_vector(args.memory)))

def add_memory_io(q):
    q.add_argument("--memory-in")
    q.add_argument("--memory-out")
    q.add_argument("--checkpoint-in")
    q.add_argument("--checkpoint-out")

def add_event_args(q):
    q.add_argument("--events", nargs="*")
    q.add_argument("--events-file")
    q.add_argument("--trace")
    add_memory_io(q)


def build_cli():
    p=argparse.ArgumentParser(prog="xc",description="XC compiler/interpreter v0.3")
    sub=p.add_subparsers(dest="cmd",required=True)
    q=sub.add_parser("check"); q.add_argument("source"); q.set_defaults(func=cmd_check)
    q=sub.add_parser("compile"); q.add_argument("source"); q.add_argument("-o","--output"); q.set_defaults(func=cmd_compile)
    q=sub.add_parser("run"); q.add_argument("source"); q.add_argument("--cycles",type=int,default=5); q.add_argument("--trace"); add_memory_io(q); q.set_defaults(func=cmd_run)
    q=sub.add_parser("run-ir"); q.add_argument("ir"); q.add_argument("--cycles",type=int,default=5); q.add_argument("--trace"); add_memory_io(q); q.set_defaults(func=cmd_run_ir)
    q=sub.add_parser("run-events"); q.add_argument("source"); add_event_args(q); q.set_defaults(func=cmd_run_events)
    q=sub.add_parser("run-events-ir"); q.add_argument("ir"); add_event_args(q); q.set_defaults(func=cmd_run_events_ir)
    q=sub.add_parser("replay"); q.add_argument("source"); q.add_argument("--cycles",type=int,default=20); q.set_defaults(func=cmd_replay)
    q=sub.add_parser("replay-events"); q.add_argument("source"); q.add_argument("--events",nargs="*"); q.add_argument("--events-file"); q.set_defaults(func=cmd_replay_events)
    q=sub.add_parser("memory"); q.add_argument("memory_file"); q.add_argument("--last",type=int,default=5); q.set_defaults(func=cmd_memory)
    q=sub.add_parser("checkpoint"); q.add_argument("checkpoint_file"); q.set_defaults(func=cmd_checkpoint)
    q=sub.add_parser("recall"); q.add_argument("source"); q.add_argument("--memory",default="Episodic"); q.add_argument("--observation",required=True); q.add_argument("--top",type=int,default=5); add_memory_io(q); q.set_defaults(func=cmd_recall)
    return p

def main(argv=None):
    args=build_cli().parse_args(argv)
    try: args.func(args); return 0
    except XCError as exc:
        print(f"XC ERROR: {exc}",file=sys.stderr); return 2

if __name__=="__main__": raise SystemExit(main())
