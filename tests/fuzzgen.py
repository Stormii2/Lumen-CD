"""
Random *valid* Lumen program generator, used to test the optimiser by
differential execution: every generated program is run unoptimised and
optimised, and the two must print the same output and fail with the same
run-time error (or none).

The programs are typed, scoped, and deliberately mean: shadowing, global
variables written by functions, short-circuit operators whose right side has a
side effect, arrays aliased through function parameters, integer values near
the 64-bit limit, and divisions whose divisor may be zero. Loops are bounded
and functions only call lower-numbered functions, so every program terminates.
"""

import random

BIG = [4611686018427387904, 9223372036854775807, 3037000500, 2, 3, 7]


class Gen:
    def __init__(self, seed: int, risky: bool = True):
        self.risky = risky
        self.r = random.Random(seed)
        self.n = 0
        self.funcs = []          # (name, arity, rettype)
        self.globals = []        # (name, type)
        self.lines = []
        self.fn_ctx = None

    def fresh(self, p="v"):
        self.n += 1
        return f"{p}{self.n}"

    # ---- expressions ------------------------------------------------------
    def vars_of(self, env, ty):
        return [n for scope in env for (n, t) in scope if t == ty]

    def int_expr(self, env, d):
        r = self.r
        choices = ["lit", "var"] if d <= 0 else ["lit", "var", "bin", "bin", "bin", "neg", "call", "arr", "divmod"]
        vs = self.vars_of(env, "int")
        c = "var" if (vs and r.random() < 0.45) else r.choice(choices)
        if c == "var" and vs:
            return r.choice(vs)
        if c == "bin":
            return f"({self.int_expr(env, d-1)} {r.choice(['+', '-', '*'])} {self.int_expr(env, d-1)})"
        if c == "neg":
            return f"(-{self.int_expr(env, d-1)})"
        if c == "divmod":
            return f"({self.int_expr(env, d-1)} {r.choice(['/', '%'])} {self.int_expr(env, d-1) if (self.risky and r.random() < .5) else r.choice(['2', '3', '-1', '0'] if self.risky else ['2', '3', '5'])})"
        if c == "call":
            fs = [f for f in self.funcs if f[2] == "int" and (self.fn_ctx is None or f[3] < self.fn_ctx)]
            if fs:
                f = r.choice(fs)
                return f"{f[0]}({', '.join(self.int_expr(env, d-1) for _ in range(f[1]))})"
        if c == "arr":
            arrs = self.vars_of(env, "int[]")
            if arrs:
                idx = r.choice(["0", "1", "2", self.int_expr(env, d - 1)]) if (self.risky and r.random() < .5) else r.choice(["0", "1", "2"])
                return f"{r.choice(arrs)}[{idx}]"
        return str(r.choice([0, 1, 2, 3, 5, 10, 100, r.choice(BIG) if self.risky else 7]))

    def float_expr(self, env, d):
        r = self.r
        vs = self.vars_of(env, "float")
        c = "var" if (vs and r.random() < 0.45) else r.choice(["lit", "var", "bin", "mix", "div"] if d > 0 else ["lit", "var"])
        if c == "var" and vs:
            return r.choice(vs)
        if c == "bin":
            return f"({self.float_expr(env, d-1)} {r.choice(['+', '-', '*'])} {self.float_expr(env, d-1)})"
        if c == "mix":
            return f"({self.float_expr(env, d-1)} + {self.int_expr(env, d-1)})"
        if c == "div":
            return f"({self.float_expr(env, d-1)} / {r.choice(['2.0', '4', self.float_expr(env, d-1)])})"
        return r.choice(["0.5", "1.5", "2.25", "10.0", "0.0"])

    def bool_expr(self, env, d):
        r = self.r
        vs = self.vars_of(env, "bool")
        c = r.choice(["cmp", "cmp", "var", "lit", "and", "or", "not", "fcmp"] if d > 0 else ["lit", "var", "cmp"])
        if c == "var" and vs:
            return r.choice(vs)
        if c == "cmp":
            return f"({self.int_expr(env, d-1)} {r.choice(['<', '>', '<=', '>=', '==', '!='])} {self.int_expr(env, d-1)})"
        if c == "fcmp":
            return f"({self.float_expr(env, d-1)} {r.choice(['<', '>', '==', '!='])} {self.float_expr(env, d-1)})"
        if c == "and":
            return f"({self.bool_expr(env, d-1)} && {self.bool_expr(env, d-1)})"
        if c == "or":
            return f"({self.bool_expr(env, d-1)} || {self.bool_expr(env, d-1)})"
        if c == "not":
            return f"(!{self.bool_expr(env, d-1)})"
        return r.choice(["true", "false"])

    def str_expr(self, env, d):
        vs = self.vars_of(env, "string")
        if vs and self.r.random() < .5:
            return self.r.choice(vs)
        if d > 0 and self.r.random() < .4:
            return f"({self.str_expr(env, d-1)} + {self.str_expr(env, d-1)})"
        return self.r.choice(['"a"', '"bc"', '""'])

    def expr(self, env, ty, d):
        return {"int": self.int_expr, "float": self.float_expr, "bool": self.bool_expr,
                "string": self.str_expr}[ty](env, d)

    # ---- statements ---------------------------------------------------------
    def block(self, env, depth, ind, allow_ret_fn=None, loops=0):
        out = []
        env = env + [[]]
        for _ in range(self.r.randint(1, 4)):
            out += self.stmt(env, depth, ind, loops)
        return out

    def stmt(self, env, depth, ind, loops):
        r = self.r
        pad = "    " * ind
        kinds = ["decl", "decl", "assign", "assign", "print", "print", "arrset", "call"]
        if depth > 0:
            kinds += ["if", "if", "while"]
        k = r.choice(kinds)
        scope = env[-1]
        if k == "decl":
            ty = r.choice(["int", "int", "int", "float", "bool", "string", "int[]"])
            name = self.fresh()
            if ty == "int[]":
                e = "[" + ", ".join(self.int_expr(env, 1) for _ in range(3)) + "]"
            else:
                e = self.expr(env, ty, 2)
            scope.append((name, ty))
            return [f"{pad}{ty} {name} = {e};"]
        if k == "assign":
            cands = [(n, t) for sc in env for (n, t) in sc if t in ("int", "float", "bool", "string")
                     and not n.startswith("c")] + [(n, t) for (n, t) in self.globals if t in ("int", "float", "bool")]
            if not cands:
                return [f"{pad}print(1);"]
            n, t = r.choice(cands)
            return [f"{pad}{n} = {self.expr(env, t, 2)};"]
        if k == "print":
            ts = r.choice(["int", "int", "float", "bool", "string"])
            return [f"{pad}print({self.expr(env, ts, 2)});"]
        if k == "arrset":
            arrs = self.vars_of(env, "int[]")
            if not arrs:
                return [f"{pad}print(0);"]
            return [f"{pad}{r.choice(arrs)}[{r.choice(['0', '1', '2', '2', self.int_expr(env, 1)] if self.risky else ['0', '1', '2'])}] = {self.int_expr(env, 2)};"]
        if k == "call":
            fs = [f for f in self.funcs if f[2] == "void" and (self.fn_ctx is None or f[3] < self.fn_ctx)]
            if not fs:
                return [f"{pad}print(2);"]
            f = r.choice(fs)
            args = []
            for t in f[4]:
                args.append(self.expr(env, t, 1) if t != "int[]" else (self.vars_of(env, "int[]") or ["[1, 2, 3]"])[0])
            return [f"{pad}{f[0]}({', '.join(args)});"]
        if k == "if":
            out = [f"{pad}if ({self.bool_expr(env, 2)}) {{"]
            out += self.block(env, depth - 1, ind + 1, loops=loops)
            if r.random() < .6:
                out += [f"{pad}}} else {{"] + self.block(env, depth - 1, ind + 1, loops=loops)
            out.append(f"{pad}}}")
            return out
        if k == "while" and loops < 2:
            c = self.fresh("c")
            n = r.randint(0, 5)
            env[-1].append((c, "int"))
            out = [f"{pad}int {c} = 0;", f"{pad}while ({c} < {n}) {{"]
            out += self.block(env, depth - 1, ind + 1, loops=loops + 1)
            out.append(f"{pad}    {c} = {c} + 1;")
            out.append(f"{pad}}}")
            return out
        return [f"{pad}print(3);"]

    # ---- program ----------------------------------------------------------------
    def program(self) -> str:
        r = self.r
        L = []
        for _ in range(r.randint(0, 3)):
            ty = r.choice(["int", "int", "float", "bool"])
            name = self.fresh("g")
            L.append(f"{ty} {name} = {self.expr([], ty, 1)};")
            self.globals.append((name, ty))
        for i in range(r.randint(0, 4)):
            ret = r.choice(["int", "int", "void"])
            ptypes = [r.choice(["int", "int", "float", "int[]"]) if ret == "void" else "int" for _ in range(r.randint(0, 3))]
            name = f"f{i}"
            self.fn_ctx = i
            plist = [(self.fresh("p"), t) for t in ptypes]
            env = [[(n, t) for (n, t) in self.globals], plist]
            params = ", ".join(f"{t} {n}" for (n, t) in plist)
            body = []
            for _ in range(r.randint(2, 6)):
                body += self.stmt(env + [[]], 2, 1, 0)
            env2 = env + [[]]
            if ret == "int":
                body.append(f"    return {self.int_expr(env, 2)};")
            L.append(f"func {ret} {name}({params}) {{")
            L += body
            L.append("}")
            self.funcs.append((name, len(ptypes), ret, i, ptypes))
        self.fn_ctx = None
        env = [[(n, t) for (n, t) in self.globals]]
        for _ in range(r.randint(8, 20)):
            L += self.stmt(env, 3, 0, 0)
        return "\n".join(L) + "\n"


def generate(seed: int, risky: bool = True) -> str:
    return Gen(seed, risky).program()
