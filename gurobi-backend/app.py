"""HTTP backend for the website's Gurobi SAN + weighted A* mode."""
from __future__ import annotations

import heapq
import json
import math
import os
import time
from collections import Counter, defaultdict
from typing import Any

import gurobipy as gp
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


BACKEND_VERSION = "1.2.0"
SAN_MODEL_MAX_SECONDS = max(.05, float(os.getenv("GUROBI_SAN_MODEL_MAX_SECONDS", "1.0")))
SAN_FAST_MAX_SECONDS = max(.03, float(os.getenv("GUROBI_SAN_FAST_MAX_SECONDS", ".30")))
SAN_REFINE_SOURCE_LIMIT = max(1, int(os.getenv("GUROBI_SAN_REFINE_SOURCES", "2")))
GUROBI_THREADS = max(0, int(os.getenv("GUROBI_THREADS", "0")))

app = FastAPI(title="SAN-A* Gurobi API", version=BACKEND_VERSION)
origins = [v.strip() for v in os.getenv("ALLOWED_ORIGINS", "*").split(",") if v.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["POST", "GET"], allow_headers=["*"])


class SolveBody(BaseModel):
    model_config = {"extra": "allow"}


def validate(raw: dict[str, Any]) -> dict[str, Any]:
    c = json.loads(json.dumps(raw))
    if not 1 <= len(c.get("cars", [])) <= 40:
        raise ValueError("请配置1～40辆车。")
    if not 2 <= len(c.get("tracks", [])) <= 12:
        raise ValueError("请配置2～12条股道。")
    ids = [x["id"] for x in c["cars"]]
    if len(ids) != len(set(ids)) or any(not x for x in ids):
        raise ValueError("车辆ID必须非空且唯一。")
    c.setdefault("goalMode", "custom")
    c.setdefault("destinationOrder", list(dict.fromkeys(str(x["destination"]) for x in c["cars"])))
    c.setdefault("objective", "time")
    c.setdefault("weight", 350.0)
    c.setdefault("poolSize", 100)
    c.setdefault("timeLimit", 10.0)
    c.setdefault("searchStrategy", "auto")
    if c["searchStrategy"] not in ("auto", "astar", "dfs", "brfs", "best", "cbfs", "sanr"):
        raise ValueError("未知序列分支树遍历策略。")
    c.setdefault("initialTrack", 1)
    c.setdefault("shuntingSpeed", 16.0)
    c.setdefault("ladderSpeed", 10.0)
    c.setdefault("trackSpacing", 3.0)
    c.setdefault("timeConstant", 13500.0)
    c.setdefault("timePerCar", 517.5)
    defaults = dict(shunting1=1, shunting2=2, shunting3=0, skipping1=1, skipping2=2, holding1=0, holding2=.1, virtual2=0)
    c["rewards"] = {**defaults, **c.get("rewards", {})}
    car_ids = set(ids)
    seen = []
    for track in c["tracks"]:
        seen += track.get("initial", [])
    if Counter(seen) != Counter(ids):
        raise ValueError("初始状态必须让每辆车恰好出现一次。")
    if c["goalMode"] == "custom":
        goal = [x for t in c["tracks"] for x in t.get("goal", [])]
        if Counter(goal) != Counter(ids):
            raise ValueError("自定义目标必须让每辆车恰好出现一次。")
    else:
        present = set(str(x["destination"]) for x in c["cars"])
        if set(c["destinationOrder"]) != present or len(c["destinationOrder"]) != len(present):
            raise ValueError("论文模式去向顺序必须恰好包含全部去向。")
        total = sum(float(x["length"]) for x in c["cars"])
        if not any(float(t["capacity"]) >= total for t in c["tracks"]):
            raise ValueError("论文模式要求至少一条股道能够容纳全部车辆。")
    if not 1 <= int(c["initialTrack"]) <= len(c["tracks"]):
        raise ValueError("初始机车股道编号超出范围。")
    if float(c["locomotive"]) > float(c["headshunt"]):
        raise ValueError("牵出线不能短于机车。")
    if any(x not in car_ids for t in c["tracks"] for x in t.get("initial", [])):
        raise ValueError("初始状态包含未知车辆。")
    return c


class Solver:
    def __init__(self, config: dict[str, Any]):
        self.c = validate(config)
        self.cars = {x["id"]: x for x in self.c["cars"]}
        self.deadline = time.perf_counter() + float(self.c["timeLimit"])
        self.order = self.c["destinationOrder"] if self.c["goalMode"] == "paper" else self._custom_order()
        self.ordered = set(zip(self.order, self.order[1:]))
        self.stats = dict(expanded=0, generated=0, dominancePruned=0, boundPruned=0, capacityPruned=0,
                          sanModels=0, sanBbNodes=0, sanRewardBoundPruned=0, sanFeasibleSolutions=0,
                          sanPoolTruncated=0, gurobiTimeLimitedModels=0, gurobiSolutionLimitedModels=0,
                          gurobiModelsWithoutSolution=0, gurobiFastModels=0, gurobiImproveModels=0,
                          gurobiFastCandidates=0, gurobiImproveCandidates=0, hybridStructuredCandidates=0,
                          acceptedNodes=0, elapsedSeconds=0.0)

    def san_model_time_limit(self, active_sources: int, phase: str = "fast") -> float:
        remaining = max(.01, self.deadline - time.perf_counter())
        total = float(self.c["timeLimit"])
        if phase == "improve":
            ceiling = min(SAN_MODEL_MAX_SECONDS, max(.10, total * .025))
            fair_share = max(.08, remaining / max(10, active_sources * 10))
        else:
            ceiling = min(SAN_FAST_MAX_SECONDS, max(.03, total * .01))
            fair_share = max(.03, remaining / max(16, active_sources * 16))
        return max(.01, min(remaining, ceiling, fair_share))

    def _custom_order(self):
        seq = [str(self.cars[x]["destination"]) for t in self.c["tracks"] for x in t.get("goal", [])]
        return [x for i, x in enumerate(seq) if i == 0 or x != seq[i - 1]]

    def metres(self, row):
        return sum(float(self.cars[x]["length"]) for x in row)

    def relation(self, a, b):
        da, db = str(self.cars[a]["destination"]), str(self.cars[b]["destination"])
        return 2 if da == db else 1 if (da, db) in self.ordered else 3

    def links(self, state):
        counts, adjacent = Counter(), 0
        for row in state[0]:
            counts.update(str(self.cars[x]["destination"]) for x in row)
            adjacent += sum(str(self.cars[a]["destination"]) == str(self.cars[b]["destination"]) for a, b in zip(row, row[1:]))
        return sum(n * (n - 1) for n in counts.values()) - 2 * adjacent

    def is_goal(self, state):
        tracks, _ = state
        if self.c["goalMode"] == "custom":
            return tracks == tuple(tuple(t["goal"]) for t in self.c["tracks"])
        for row in tracks:
            if len(row) != len(self.c["cars"]):
                continue
            ds = [str(self.cars[x]["destination"]) for x in row]
            blocks = [x for i, x in enumerate(ds) if i == 0 or x != ds[i - 1]]
            if blocks == self.c["destinationOrder"]:
                return True
        return False

    def timing(self, kind, old_track, track, before, after):
        speed = float(self.c["shuntingSpeed"]) / 3600
        travel = float(self.c["tracks"][track - 1]["capacity"]) / (1000 * speed)
        leg = lambda n: travel + .5 * speed * (float(self.c["timeConstant"]) + float(self.c["timePerCar"]) * n)
        ladder = abs(old_track - track) * float(self.c["trackSpacing"]) / (1000 * (float(self.c["ladderSpeed"]) / 3600))
        entry = leg(after if kind == "PULL" else before)
        exit_ = leg(before if kind == "PULL" else after)
        return dict(ladderSeconds=ladder, entrySeconds=entry, exitSeconds=exit_, durationSeconds=ladder + entry + exit_)

    def arcs(self, state, k):
        source, rewards = state[0][k], self.c["rewards"]
        arcs, outgoing, incoming = [], [[] for _ in source], defaultdict(list)
        def add(i, j, target, kind, reward, to):
            endpoint = ("root", target) if j is None else ("car", j)
            a = dict(name=f"x_{len(arcs)+1}", **{"from": source[i]}, to=to, sourcePosition=i+1,
                     successorPosition=None if j is None else j+1, targetTrack=None if target is None else target+1,
                     type=kind, reward=float(reward), i=i, j=j, target=target, endpoint=endpoint)
            outgoing[i].append(len(arcs)); incoming[endpoint].append(len(arcs)); arcs.append(a)
        for i in range(len(source)):
            for j in range(i + 1, len(source)):
                r = self.relation(source[i], source[j])
                if j == i + 1:
                    add(i, j, None, "holding-2" if r == 2 else "holding-1", rewards["holding2"] if r == 2 else rewards["holding1"], source[j])
                elif r != 3:
                    add(i, j, None, f"skipping-{r}", rewards["skipping2"] if r == 2 else rewards["skipping1"], source[j])
            for target in range(len(state[0])):
                if target == k:
                    add(i, None, target, "virtual-2", rewards["virtual2"], f"V{target+1}")
                elif not state[0][target]:
                    add(i, None, target, "virtual-1", 1 / (2 * float(self.c["tracks"][target]["capacity"])), f"V{target+1}")
                else:
                    to, r = state[0][target][0], self.relation(source[i], state[0][target][0])
                    add(i, None, target, f"shunting-{r}", rewards[f"shunting{r}"], to)
        return source, arcs, outgoing, incoming

    @staticmethod
    def public_arc(a):
        return {k: a[k] for k in ("name", "from", "to", "type", "reward", "sourcePosition", "successorPosition", "targetTrack")}

    def decode(self, state, k, source, arcs, chosen):
        targets, selected = [None] * len(source), []
        for i in range(len(source) - 1, -1, -1):
            ai = chosen[i]; a = arcs[ai]; selected.append(ai)
            targets[i] = a["target"] if a["j"] is None else targets[a["j"]]
        count = len(source)
        while count and targets[count - 1] == k:
            count -= 1
        if not count:
            return None
        pulled = source[:count]
        if self.metres(pulled) + float(self.c["locomotive"]) > float(self.c["headshunt"]):
            return None
        tracks = [list(x) for x in state[0]]; tracks[k] = list(source[count:])
        load, position, total, operations = list(pulled), state[1], 0.0, []
        def record(kind, track, cars, before, after):
            nonlocal position, total
            ts = self.timing(kind, position, track + 1, before, after); total += ts["durationSeconds"]; position = track + 1
            operations.append(dict(kind=kind, track=track+1, trackName=self.c["tracks"][track]["name"], cars=list(cars), before=before, after=after,
                                   **ts, state=dict(tracks=[list(x) for x in tracks], load=list(load), position=position)))
        record("PULL", k, pulled, 0, len(load))
        p = count - 1
        while p >= 0:
            target, start = targets[p], p
            while start > 0 and targets[start - 1] == target:
                start -= 1
            moved, before = source[start:p+1], len(load); del load[-len(moved):]; tracks[target] = list(moved) + tracks[target]
            if self.metres(tracks[target]) > float(self.c["tracks"][target]["capacity"]) + 1e-8:
                return None
            record("PUSH", target, moved, before, len(load)); p = start - 1
        new_state = (tuple(tuple(x) for x in tracks), position)
        reward = sum(arcs[x]["reward"] for x in selected)
        constraints = ([dict(name=f"out_{i+1}", variables=[arcs[x]["name"] for x in row], sense="=", rhs=1) for i, row in enumerate(self._last_outgoing)] +
                       [dict(name=f"in_{i+1}", variables=[arcs[x]["name"] for x in row], sense="<=", rhs=1) for i, row in enumerate(self._last_incoming.values())])
        constraints.append(dict(name="effective_cross_track", variables=[a["name"] for a in arcs if a["target"] is not None and a["target"] != k], sense=">=", rhs=1))
        action = dict(sourceTrack=k+1, sourceTrackName=self.c["tracks"][k]["name"], pulledCars=list(pulled), reward=reward,
                      durationSeconds=total, cost=total if self.c["objective"] == "time" else 1, operations=operations,
                      model=dict(formulation="SAN-0-1-Gurobi", t={f"t_{i+1}": int(i == k) for i in range(len(tracks))},
                                 x={a["name"]: int(i in selected) for i, a in enumerate(arcs)},
                                 selectedArcs=[self.public_arc(arcs[i]) for i in selected], candidateArcs=[self.public_arc(a) for a in arcs],
                                 linearConstraints=constraints, capacityRule="solution decoded and checked against effective track length"))
        return new_state, action

    def candidates(self, state, phase="fast", base_cost=0.0, incumbent_bound=math.inf):
        candidates, restricted = {}, False
        active_sources = max(1, sum(bool(row) for row in state[0]))
        source_scores = sorted(
            ((len(row) + 2 * sum(self.cars[a]["destination"] != self.cars[b]["destination"]
                                 for a, b in zip(row, row[1:])), k)
             for k, row in enumerate(state[0]) if row),
            reverse=True,
        )
        refine_sources = ({k for _, k in source_scores[:SAN_REFINE_SOURCE_LIMIT]}
                          if phase == "improve" and math.isfinite(incumbent_bound) else set())
        for k, row in enumerate(state[0]):
            if not row or time.perf_counter() >= self.deadline:
                continue
            source, arcs, outgoing, incoming = self.arcs(state, k); self._last_outgoing, self._last_incoming = outgoing, incoming
            model = gp.Model(f"SAN_track_{k+1}"); model.Params.OutputFlag = 0
            model.Params.TimeLimit = self.san_model_time_limit(active_sources, "fast")
            model.Params.PoolSearchMode = 1
            model.Params.MIPFocus = 1
            model.Params.Seed = 0
            if GUROBI_THREADS: model.Params.Threads = GUROBI_THREADS
            requested = int(self.c["poolSize"]); effective_pool = requested if requested else 10000
            model.Params.PoolSolutions = effective_pool
            model.Params.SolutionLimit = effective_pool
            x = model.addVars(len(arcs), vtype=gp.GRB.BINARY, name="x")
            for idxs in outgoing: model.addConstr(gp.quicksum(x[i] for i in idxs) == 1)
            for idxs in incoming.values(): model.addConstr(gp.quicksum(x[i] for i in idxs) <= 1)
            cross = [i for i, a in enumerate(arcs) if a["target"] is not None and a["target"] != k]
            model.addConstr(gp.quicksum(x[i] for i in cross) >= 1)
            model.setObjective(gp.quicksum(arcs[i]["reward"] * x[i] for i in range(len(arcs))), gp.GRB.MAXIMIZE)
            def collect(stage):
                added = 0
                for solution in range(model.SolCount):
                    model.Params.SolutionNumber = solution
                    chosen = {}
                    for i, idxs in enumerate(outgoing):
                        selected = [a for a in idxs if x[a].Xn > .5]
                        if len(selected) != 1: break
                        chosen[i] = selected[0]
                    if len(chosen) != len(source): continue
                    decoded = self.decode(state, k, source, arcs, chosen)
                    if not decoded: self.stats["capacityPruned"] += 1; continue
                    child, action = decoded; self.stats["sanFeasibleSolutions"] += 1
                    if base_cost + action["cost"] >= incumbent_bound:
                        self.stats["boundPruned"] += 1; continue
                    if child not in candidates or action["cost"] < candidates[child]["cost"]:
                        if child not in candidates: added += 1
                        candidates[child] = action
                self.stats[f"gurobi{stage.title()}Candidates"] += added

            def record_pass(stage):
                nonlocal restricted
                self.stats["sanModels"] += 1; self.stats["sanBbNodes"] += int(model.NodeCount)
                self.stats[f"gurobi{stage.title()}Models"] += 1
                if model.Status == gp.GRB.TIME_LIMIT:
                    restricted = True; self.stats["gurobiTimeLimitedModels"] += 1
                if model.Status == gp.GRB.SOLUTION_LIMIT:
                    restricted = True; self.stats["gurobiSolutionLimitedModels"] += 1
                if model.SolCount == 0: self.stats["gurobiModelsWithoutSolution"] += 1
                if requested and model.SolCount >= requested: restricted = True

            model.optimize(); record_pass("fast"); collect("fast")
            if k in refine_sources and time.perf_counter() < self.deadline:
                model.Params.TimeLimit = self.san_model_time_limit(active_sources, "improve")
                model.Params.PoolSearchMode = 2
                model.Params.MIPFocus = 2
                model.Params.SolutionLimit = 2000000000
                model.optimize(); record_pass("improve"); collect("improve")
            model.dispose()

        # Merge deterministic whole-group actions so a time-limited MIP pool cannot
        # hide the simplest feasible moves from the outer state search.
        for source_index, row in enumerate(state[0]):
            if not row: continue
            for target_index in range(len(state[0])):
                moved = self.group_move(state, source_index, target_index)
                if moved is None: continue
                child, action = moved
                if base_cost + action["cost"] >= incumbent_bound: continue
                if child not in candidates or action["cost"] < candidates[child]["cost"]:
                    if child not in candidates: self.stats["hybridStructuredCandidates"] += 1
                    candidates[child] = action
        if restricted: self.stats["sanPoolTruncated"] += 1
        return [(s, a) for s, a in candidates.items()], restricted

    def group_move(self, state, source_index, target_index):
        if source_index == target_index or not state[0][source_index]:
            return None
        row = state[0][source_index]
        destination = self.cars[row[0]]["destination"]
        count = 1
        while count < len(row) and self.cars[row[count]]["destination"] == destination:
            count += 1
        if self.metres(row[:count]) + float(self.c["locomotive"]) > float(self.c["headshunt"]) + 1e-8:
            return None
        if self.metres(state[0][target_index]) + self.metres(row[:count]) > float(self.c["tracks"][target_index]["capacity"]) + 1e-8:
            return None
        source, arcs, outgoing, incoming = self.arcs(state, source_index)
        self._last_outgoing, self._last_incoming = outgoing, incoming
        chosen = {}
        for i in range(len(row)):
            target = target_index if i < count else source_index
            next_in_segment = i + 1 < len(row) and ((i + 1 < count) == (i < count))
            matches = [ai for ai in outgoing[i] if
                       (arcs[ai]["j"] == i + 1 if next_in_segment else
                        arcs[ai]["j"] is None and arcs[ai]["target"] == target)]
            if not matches:
                return None
            chosen[i] = matches[0]
        return self.decode(state, source_index, source, arcs, chosen)

    def constructive_seed(self, initial):
        tracks = self.c["tracks"]
        count = len(self.cars)
        total_length = sum(float(car["length"]) for car in self.c["cars"])
        targets = ([i for i, track in enumerate(tracks) if len(track.get("goal", [])) == count]
                   if self.c["goalMode"] == "custom" else
                   [i for i, track in enumerate(tracks) if float(track["capacity"]) + 1e-8 >= total_length])
        if not targets:
            return None

        def fixed(state, track):
            row = state[0][track]
            if self.c["goalMode"] == "custom":
                goal = tracks[track]["goal"]
                n = 0
                while n < min(len(row), len(goal)) and row[-n-1] == goal[-n-1]:
                    n += 1
                return n
            n = 0
            while n < len(row) and self.cars[row[-n-1]]["destination"] == self.c["destinationOrder"][-1]:
                n += 1
            return n

        def rank(state):
            grouped = sum(len(row) - sum(self.cars[a]["destination"] != self.cars[b]["destination"]
                                         for a, b in zip(row, row[1:])) for row in state[0])
            return (count - max(fixed(state, track) for track in targets)) * 100 - grouped

        nodes = [(initial, None, None, 0)]
        visited = {initial: 0}
        heap = [(rank(initial), 0)]
        limit = min(self.deadline, time.perf_counter() + min(1.2, float(self.c["timeLimit"]) * .15))
        expanded = 0
        while heap and time.perf_counter() < limit and expanded < 5000:
            _, parent = heapq.heappop(heap)
            state, _, _, depth = nodes[parent]
            expanded += 1
            if depth >= count + 8:
                continue
            for source in range(len(tracks)):
                for target in range(len(tracks)):
                    moved = self.group_move(state, source, target)
                    if moved is None:
                        continue
                    child, action = moved
                    if visited.get(child, math.inf) <= depth + 1:
                        continue
                    visited[child] = depth + 1
                    idx = len(nodes)
                    nodes.append((child, parent, action, depth + 1))
                    if self.is_goal(child):
                        path = []
                        while nodes[idx][1] is not None:
                            path.append((nodes[idx][0], nodes[idx][2]))
                            idx = nodes[idx][1]
                        return list(reversed(path))
                    heapq.heappush(heap, (rank(child) + (depth + 1) * .01, idx))
        return None

    def san_r_seed(self, initial):
        original_deadline = self.deadline
        self.deadline = min(original_deadline, time.perf_counter() + min(.5, float(self.c["timeLimit"]) * .08))
        state, seen, path = initial, {initial}, []
        try:
            for _ in range(len(self.cars) * 3):
                if time.perf_counter() >= self.deadline:
                    break
                candidates, _ = self.candidates(state, phase="fast")
                candidates = [(s,a) for s,a in candidates if s not in seen]
                if not candidates:
                    break
                state, action = min(candidates, key=lambda pair: (-pair[1]["reward"], self.links(pair[0]), pair[1]["cost"]))
                path.append((state, action)); seen.add(state)
                if self.is_goal(state):
                    return path
            return None
        finally:
            self.deadline = original_deadline

    def solve(self):
        started = time.perf_counter(); initial = (tuple(tuple(t["initial"]) for t in self.c["tracks"]), int(self.c["initialTrack"]))
        nodes = [dict(state=initial, g=0.0, seconds=0.0, parent=None, action=None, depth=0)]
        best, serial, open_ids = {initial: 0.0}, 0, {0}
        solution_history, incumbent_history, solution_history_limit, feasible_solution_count = [], [], 2000, 0
        def record_feasible(**entry):
            nonlocal feasible_solution_count
            feasible_solution_count += 1; item = dict(index=feasible_solution_count, **entry)
            if item.get("becameIncumbent"):
                incumbent_history.append(item)
            if len(solution_history) < solution_history_limit:
                solution_history.append(item)
            else:
                slot = ((feasible_solution_count * 2654435761) & 0xffffffff) % feasible_solution_count
                if slot < solution_history_limit:
                    solution_history[slot] = item
        auto = self.c["searchStrategy"] == "auto"
        difficulty = len(self.c["cars"]) * max(1, len(self.c["tracks"]) - 1) * (1.35 if self.c["goalMode"] == "custom" else 1)
        if not auto:
            level, strategies = None, (self.c["searchStrategy"],)
        elif difficulty <= 30:
            level, strategies = "较小", ("astar", "best", "brfs")
        elif difficulty <= 75:
            level, strategies = "中等", ("astar", "best", "cbfs")
        else:
            level, strategies = "较大", ("best", "astar", "dfs")
        strategy, auto_index = strategies[0], 0
        switch_at = (started + float(self.c["timeLimit"]) * .6, started + float(self.c["timeLimit"]) * .85) if auto else ()
        attempts, segment_started, segment_expanded = [], started, 0
        def rebuild_frontier(active):
            nonlocal serial
            rebuilt, rebuilt_contours = [], defaultdict(list)
            for idx in sorted(open_ids):
                serial += 1; node = nodes[idx]
                if active == "cbfs":
                    heapq.heappush(rebuilt_contours[node["depth"]], (self.links(node["state"]), serial, idx))
                elif active in ("dfs", "brfs"):
                    rebuilt.append(idx)
                else:
                    score = node["g"] + float(self.c["weight"]) * self.links(node["state"]) if active == "astar" else self.links(node["state"])
                    heapq.heappush(rebuilt, (score, serial, idx))
            return rebuilt, rebuilt_contours, 0, 0
        heap, contours, cursor, fifo_head = rebuild_frontier(strategy)
        incumbent, upper, reason, restricted = (0, 0.0, "initial_is_goal", False) if self.is_goal(initial) else (None, math.inf, "exhausted", False)
        seed_method, solution_strategy = None, strategy
        if incumbent == 0:
            record_feasible(foundAtSeconds=0.0, objectiveValue=0.0, totalTimeSeconds=0.0, roundCount=0,
                            strategy=strategy, becameIncumbent=True, source="initial")
        if incumbent is None:
            seed = self.san_r_seed(initial)
            if seed:
                seed_method = "SAN-R"
            elif self.c["searchStrategy"] != "sanr":
                seed = self.constructive_seed(initial)
                if seed:
                    seed_method = "车组构造辅助"
            if seed:
                parent = 0
                for state, action in seed:
                    prior = nodes[parent]
                    nodes.append(dict(state=state, g=prior["g"]+action["cost"], seconds=prior["seconds"]+action["durationSeconds"],
                                      parent=parent, action=action, depth=prior["depth"]+1))
                    parent = len(nodes)-1
                incumbent, upper = parent, nodes[parent]["g"]
                record_feasible(foundAtSeconds=time.perf_counter()-started, objectiveValue=upper,
                                totalTimeSeconds=nodes[parent]["seconds"], roundCount=nodes[parent]["depth"],
                                strategy="sanr" if seed_method == "SAN-R" else "constructive",
                                becameIncumbent=True, source="seed")
        if incumbent == 0:
            heap.clear(); contours.clear(); open_ids.clear()
        elif self.c["searchStrategy"] == "sanr":
            heap.clear(); contours.clear(); open_ids.clear()
            reason = "seed_only" if incumbent is not None else "sanr_stalled"
        while heap or any(contours.values()):
            tick = time.perf_counter()
            if tick >= self.deadline: reason = "time_limit"; break
            if auto and auto_index < len(switch_at) and tick >= switch_at[auto_index]:
                attempts.append(dict(strategy=strategy, elapsedSeconds=tick-segment_started,
                                     expanded=self.stats["expanded"]-segment_expanded,
                                     bestCost=None if math.isinf(upper) else upper))
                segment_started, segment_expanded = tick, self.stats["expanded"]
                auto_index += 1; strategy = strategies[auto_index]
                heap, contours, cursor, fifo_head = rebuild_frontier(strategy)
            if strategy == "dfs":
                idx = heap.pop()
            elif strategy == "brfs":
                idx = heap[fifo_head]; fifo_head += 1
                if fifo_head >= len(heap): heap.clear(); fifo_head = 0
            elif strategy == "cbfs":
                levels = sorted(level for level, queue in contours.items() if queue)
                level = next((level for level in levels if level >= cursor), levels[0])
                _, _, idx = heapq.heappop(contours[level]); cursor = level + 1
            else:
                _, _, idx = heapq.heappop(heap)
            open_ids.discard(idx)
            node = nodes[idx]
            if node["g"] != best.get(node["state"]): continue
            if node["g"] >= upper: self.stats["boundPruned"] += 1; continue
            self.stats["expanded"] += 1
            phase = "improve" if incumbent is not None else "fast"
            children, cut = self.candidates(node["state"], phase=phase, base_cost=node["g"], incumbent_bound=upper); restricted |= cut
            for state, action in children:
                self.stats["generated"] += 1; g = node["g"] + action["cost"]
                goal = self.is_goal(state)
                if goal:
                    record_feasible(foundAtSeconds=time.perf_counter()-started, objectiveValue=g,
                                    totalTimeSeconds=node["seconds"]+action["durationSeconds"], roundCount=node["depth"]+1,
                                    strategy=strategy, becameIncumbent=g < upper, source="search")
                if g >= upper: self.stats["boundPruned"] += 1; continue
                if g >= best.get(state, math.inf): self.stats["dominancePruned"] += 1; continue
                best[state] = g; child_id = len(nodes); nodes.append(dict(state=state, g=g, seconds=node["seconds"]+action["durationSeconds"], parent=idx, action=action, depth=node["depth"]+1))
                if self.is_goal(state): incumbent, upper, solution_strategy = child_id, g, strategy
                else:
                    open_ids.add(child_id)
                    serial += 1
                    if strategy == "cbfs":
                        heapq.heappush(contours[node["depth"]+1], (self.links(state), serial, child_id))
                    elif strategy in ("dfs", "brfs"):
                        heap.append(child_id)
                    else:
                        score = g + float(self.c["weight"]) * self.links(state) if strategy == "astar" else self.links(state)
                        heapq.heappush(heap, (score, serial, child_id))
            if restricted and time.perf_counter() >= self.deadline: reason = "time_limit"; break
        if auto:
            attempts.append(dict(strategy=strategy, elapsedSeconds=time.perf_counter()-segment_started,
                                 expanded=self.stats["expanded"]-segment_expanded,
                                 bestCost=None if math.isinf(upper) else upper))
        complete = reason in ("exhausted", "initial_is_goal") and not restricted
        status = ("optimal" if complete else "feasible") if incumbent is not None else ("infeasible" if complete else "no_solution_found")
        path = []
        if incumbent is not None:
            i = incumbent
            while nodes[i]["parent"] is not None: path.append(nodes[i]); i = nodes[i]["parent"]
            path.reverse()
        cumulative, operations, steps = 0.0, [], []
        for round_no, node in enumerate(path, 1):
            action = json.loads(json.dumps(node["action"]))
            for op in action["operations"]:
                cumulative += op["durationSeconds"]; op.update(step=len(operations)+1, round=round_no, cumulativeSeconds=cumulative); operations.append(op)
            state = node["state"]; lam = self.links(state)
            action.update(step=round_no, cumulativeCost=node["g"], cumulativeSeconds=cumulative, **{"lambda": lam}, priority=node["g"]+float(self.c["weight"])*lam,
                          state=dict(tracks=[list(x) for x in state[0]], load=[], position=state[1])); steps.append(action)
        total = None if incumbent is None else nodes[incumbent]["seconds"]
        breakdown = None if total is None else {f: sum(op[f] for op in operations) for f in ("ladderSeconds", "entrySeconds", "exitSeconds")}
        self.stats["acceptedNodes"], self.stats["elapsedSeconds"] = len(nodes), time.perf_counter() - started
        return dict(schemaVersion=4, backendVersion=BACKEND_VERSION,
                    gurobiSettings=dict(mode="two-stage", fastPoolSearchMode=1, improvePoolSearchMode=2,
                                        fastModelMaxSeconds=SAN_FAST_MAX_SECONDS, improveModelMaxSeconds=SAN_MODEL_MAX_SECONDS,
                                        refineSourceLimit=SAN_REFINE_SOURCE_LIMIT, threads=GUROBI_THREADS or "auto"),
                    algorithm=f"two-stage SAN 0-1 MIP (Gurobi) + structured candidate merge + feasible seed + {'shared automatic frontier' if auto else f'SAN-{strategy}'}", solverEngine="gurobi", status=status,
                    seedMethod=seed_method, searchStrategy=self.c["searchStrategy"],
                    **(dict(selectedSearchStrategy=solution_strategy, autoDifficultyLevel=level, autoSearchPlan=list(strategies), autoSearchAttempts=attempts, sharedSearchState=True) if auto else {}),
                    optimalityProven=incumbent is not None and complete, optimalityScope="当前SAN动作空间与所选目标模式", termination=reason,
                    candidatePoolRestricted=restricted, objective=self.c["objective"], weight=self.c["weight"], poolSize=self.c["poolSize"],
                    totalCost=None if incumbent is None else upper, totalTimeSeconds=total, roundCount=None if incumbent is None else len(steps),
                    operationCount=None if incumbent is None else len(operations), timeBreakdown=breakdown, parameters=self.c,
                    solutionHistory=sorted(solution_history, key=lambda item: item["index"]), incumbentHistory=incumbent_history, feasibleSolutionCount=feasible_solution_count,
                    solutionHistorySampled=feasible_solution_count > len(solution_history),
                    initial=dict(tracks=[list(x) for x in initial[0]], load=[], position=initial[1]), steps=steps, operations=operations, statistics=self.stats,
                    trackNumbers=[dict(number=i+1, name=t["name"]) for i, t in enumerate(self.c["tracks"])],
                    destinations={x["id"]: str(x["destination"]) for x in self.c["cars"]}, goalMode=self.c["goalMode"], destinationOrder=self.c["destinationOrder"],
                    timeModel="论文式(4.19)-(4.23)，使用页面自定义参数", deviations=["未实施跨轮冗余推牵合并", "未启用未量化的首轮预热规则"])


@app.get("/health")
def health():
    try:
        env = gp.Env(empty=True); env.setParam("OutputFlag", 0); env.start(); env.dispose()
        return {"ok": True, "backendVersion": BACKEND_VERSION, "gurobi": gp.gurobi.version(),
                "gurobiMode": "two-stage", "fastModelMaxSeconds": SAN_FAST_MAX_SECONDS,
                "improveModelMaxSeconds": SAN_MODEL_MAX_SECONDS,
                "poolSearchModes": [1, 2], "refineSourceLimit": SAN_REFINE_SOURCE_LIMIT,
                "threads": GUROBI_THREADS or "auto"}
    except gp.GurobiError as exc:
        raise HTTPException(status_code=503, detail=f"Gurobi许可证不可用：{exc}") from exc


@app.get("/self-test")
def self_test():
    config = dict(
        cars=[dict(id="A", destination="1", length=15), dict(id="B", destination="2", length=15),
              dict(id="C", destination="3", length=15), dict(id="D", destination="1", length=15)],
        tracks=[dict(name="I", capacity=120, initial=["D", "C"], goal=[]),
                dict(name="II", capacity=130, initial=["B", "A"], goal=[]),
                dict(name="III", capacity=140, initial=[], goal=[])],
        goalMode="paper", destinationOrder=["1", "2", "3"], headshunt=100, locomotive=20,
        timeLimit=3, objective="time", searchStrategy="astar", weight=350, poolSize=30, initialTrack=3,
        shuntingSpeed=16, ladderSpeed=10, trackSpacing=3, timeConstant=13500, timePerCar=517.5,
    )
    try:
        result = Solver(config).solve()
        return {"ok": result["totalCost"] is not None, "backendVersion": BACKEND_VERSION,
                "status": result["status"], "termination": result["termination"],
                "totalTimeSeconds": result["totalTimeSeconds"], "roundCount": result["roundCount"],
                "statistics": result["statistics"]}
    except gp.GurobiError as exc:
        raise HTTPException(status_code=503, detail=f"Gurobi自检失败：{exc}") from exc


@app.post("/solve")
def solve(body: SolveBody):
    try:
        return Solver(body.model_dump()).solve()
    except gp.GurobiError as exc:
        raise HTTPException(status_code=503, detail=f"Gurobi求解失败：{exc}") from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
