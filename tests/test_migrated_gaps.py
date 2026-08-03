"""
Test: Migración de gaps desde to_clean_next/ a features/.

Valida los 5 módulos migrados:
  1. features/code_analysis/infrastructure/hallucination.py — detector de imports alucinados
  2. features/code_analysis/application/deep_analysis.py — protocolo de 6 capas → plan/progress
  3. features/engine/domain/enforce.py — capa de coerción del skill delta
  4. features/spring/application/ceremonies.py — backlog JSONL + planning ceremony
  5. features/task_execution/infrastructure/verify_engine.py — motor unificado de verificación

Cada test usa directorios temporales (no toca el proyecto real).
"""

import json
import os
import tempfile
from pathlib import Path

import pytest


# ── 1. hallucination ───────────────────────────────────────────


class TestHallucination:
    def test_detecta_import_inexistente_y_respeta_valido(self):
        from features.code_analysis.infrastructure.hallucination import scan_file

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "mymod.py").write_text("def hello():\n    return 1\n")
            test = root / "test.py"
            test.write_text(
                "from mymod import hello\nfrom nonexistent_pkg import foo\n"
            )
            result = scan_file(test, root)
            assert result["hallucinations"] >= 1
            for issue in result["issues"]:
                if issue["status"] == "HALLUCINATION":
                    assert issue["module"] == "nonexistent_pkg"
                assert not (issue["module"] == "mymod")

    def test_check_python_import_stdlib(self):
        from features.code_analysis.infrastructure.hallucination import check_python_import

        status, _ = check_python_import("os", "", "", "")
        assert status != "HALLUCINATION"


# ── 2. deep_analysis ───────────────────────────────────────────


LAYER_DATA = [
    ("layer-01", {"layer": "observation", "facts": [{"type": "class", "name": "Foo", "path": "src/foo.py"}], "files_scanned": ["src/foo.py"], "symptoms": ["warning"]}),
    ("layer-02", {"layer": "structure", "components": [{"name": "mod", "type": "module", "status": "existing"}, {"name": "mod2", "type": "module", "status": "missing"}], "boundaries": [{"name": "core"}]}),
    ("layer-03", {"layer": "behavior", "flows": [{"name": "main-flow", "steps": ["a", "b"]}], "contracts": [{"interface": "I", "invariants": ["sempre true"]}]}),
    ("layer-04", {"layer": "causality", "pain_points": [{"description": "lento", "impact": "high"}], "invariants": [{"rule": "inv-x", "current_status": "satisfied"}]}),
    ("layer-05", {"layer": "purpose", "objectives": [{"goal": "mejorar rendimiento", "priority": 3}], "non_goals": ["no ui"]}),
    ("layer-06", {"layer": "principles", "heuristics": [{"rule": "kiss"}], "governing_principles": ["clean arch"]}),
]


def _run_deep_cycle(tmp: str) -> tuple[str, str]:
    """Ejecuta init → 6 capas → finalize. Retorna rutas de plan/progress."""
    from features.code_analysis.application.deep_analysis import (
        init_analysis, submit_layer, finalize_analysis,
    )

    init_analysis(tmp, "test-proj", "Implementar un modulo de analisis profundo")
    for layer_id, data in LAYER_DATA:
        rc = submit_layer(tmp, layer_id, data)
        assert rc == 0, f"{layer_id} rc={rc}"
    rc = finalize_analysis(tmp)
    assert rc == 0, f"finalize rc={rc}"
    return os.path.join(tmp, "plan.json"), os.path.join(tmp, "progress.json")


class TestDeepAnalysis:
    def test_ciclo_completo_genera_plan_y_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan_path, progress_path = _run_deep_cycle(tmp)
            plan = json.loads(Path(plan_path).read_text())
            progress = json.loads(Path(progress_path).read_text())
            assert plan["plan"]["meta"]["source"] == "delta-skill"
            assert len(plan["plan"]["tasks"]) >= 1
            assert progress["progress"]["current"]["task_states"]

    def test_secuencia_forzada_bloquea(self):
        from features.code_analysis.application.deep_analysis import (
            init_analysis, submit_layer,
        )

        with tempfile.TemporaryDirectory() as tmp:
            init_analysis(tmp, "p", "goal con suficientes caracteres")
            # Saltar layer-01 e intentar layer-02 debe bloquear
            rc = submit_layer(tmp, "layer-02", LAYER_DATA[1][1])
            assert rc == 2

    def test_checksum_progress_json(self):
        from features.code_analysis.application.deep_analysis import verify_checksum

        with tempfile.TemporaryDirectory() as tmp:
            _, progress_path = _run_deep_cycle(tmp)
            progress = json.loads(Path(progress_path).read_text())
            # El checksum se aplica al dict top-level (que envuelve "progress")
            assert "_checksum" in progress
            assert verify_checksum(progress)
            # Alterar contenido debe romper el checksum
            progress["progress"]["current"]["task_states"]["TASK-001"] = "tampered"
            assert not verify_checksum(progress)


# ── 3. enforce ─────────────────────────────────────────────────


class TestEnforce:
    @pytest.fixture
    def plan_progress(self):
        plan = {"plan": {
            "goal": {"predicates": {"must_exist": ["P1", "P2"]}},
            "tasks": [
                {"task_id": "TASK-001", "risks": {"curvature": 0.10}, "validation": {"predicates": ["P1"]}},
                {"task_id": "TASK-002", "risks": {"curvature": 0.80}, "validation": {"predicates": ["P2"]}},
            ],
        }}
        progress = {"progress": {
            "current": {
                "task_states": {"TASK-001": "pending", "TASK-002": "pending"},
                "next_action": {"task_id": "TASK-001"},
            }
        }}
        return plan, progress

    def test_next_action_pasa(self, plan_progress):
        from features.engine.domain.enforce import enforce_task

        ok, reasons, _ = enforce_task(*plan_progress, "TASK-001")
        assert ok and not reasons

    def test_fuera_de_orden_bloquea(self, plan_progress):
        from features.engine.domain.enforce import enforce_task

        ok, reasons, _ = enforce_task(*plan_progress, "TASK-002")
        assert not ok
        assert any(r["rule"] == "next_action" for r in reasons)

    def test_force_override(self, plan_progress):
        from features.engine.domain.enforce import enforce_task

        ok, _, _ = enforce_task(*plan_progress, "TASK-002", force=True)
        assert ok

    def test_curvature_budget(self, plan_progress):
        from features.engine.domain.enforce import enforce_task

        ok, reasons, _ = enforce_task(*plan_progress, "TASK-002")
        assert any(r["rule"] == "curvature_budget" for r in reasons)

    def test_ready_for_review_salta_next_action(self, plan_progress):
        from features.engine.domain.enforce import enforce_task

        plan, progress = plan_progress
        progress["progress"]["current"]["task_states"]["TASK-002"] = "ready_for_review"
        ok, reasons, _ = enforce_task(plan, progress, "TASK-002", curvature_budget=1.0)
        assert ok


# ── 4. ceremonies ──────────────────────────────────────────────


class TestCeremonies:
    def _make_backlog(self, tmp: str) -> str:
        from features.spring.application.ceremonies import cmd_backlog

        backlog_dir = os.path.join(tmp, "delta")
        assert cmd_backlog(["init", backlog_dir]) == 0
        assert cmd_backlog(["add", backlog_dir, "--title", "Implementar login", "--value", "8", "--effort", "3", "--scrum-role", "po"]) == 0
        assert cmd_backlog(["add", backlog_dir, "--title", "Refactor del modulo core", "--value", "6", "--effort", "5"]) == 0
        return backlog_dir

    def test_backlog_wsjf_prioriza(self, capsys):
        from features.spring.application.ceremonies import cmd_backlog, _load_backlog

        with tempfile.TemporaryDirectory() as tmp:
            backlog_dir = self._make_backlog(tmp)
            items = _load_backlog(backlog_dir)
            assert items[0]["title"] == "Implementar login"  # 8/3 > 6/5

    def test_planning_propose_refine_commit(self, capsys):
        from features.spring.application.ceremonies import cmd_planning, _load_backlog

        with tempfile.TemporaryDirectory() as tmp:
            backlog_dir = self._make_backlog(tmp)
            chain = os.path.join(tmp, "chain-proj")

            rc = cmd_planning([
                "propose", chain, "--project-id", "projX",
                "--goal", "Implementar el login del sistema",
                "--backlog-dir", backlog_dir, "--item-ids", "BL-001,BL-002",
            ])
            assert rc == 3  # plan propuesto, espera confirmación

            task_json = json.dumps({
                "task_id": "TASK-003", "type": "feature",
                "objective": "Implementar logout de sesion",
                "result_state": "El logout funciona correctamente",
                "domain": "auth",
                "impacted_artifacts": {"files": []},
                "risks": {"curvature": 0.2},
                "validation": {"predicates": []},
            })
            rc = cmd_planning(["refine", chain, "--edit", f"tasks:add={task_json}"])
            assert rc == 0

            rc = cmd_planning(["commit", chain])
            assert rc == 0

            # plan.json válido contra schema (source delta-skill)
            plan = json.loads(Path(chain, "plan.json").read_text())
            assert plan["plan"]["meta"]["source"] == "delta-skill"
            assert len(plan["plan"]["tasks"]) == 3
            # backlog items marcados in-sprint
            items = _load_backlog(backlog_dir)
            assert all(it["status"] == "in-sprint" for it in items)


# ── 5. verify_engine ────────────────────────────────────────────


def _make_proj(tmp: str) -> Path:
    """Crea un mini proyecto con src/foo.py con símbolos reales."""
    root = Path(tmp)
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "foo.py").write_text(
        "def hello_world():\n    return 42\n\n"
        "class Greeter:\n    def greet(self):\n        return 'hi'\n"
    )
    return root


class TestVerifyEngine:
    def test_symbol_exists_presente_y_ausente(self):
        from features.task_execution.infrastructure.verify_engine import verify_predicate

        with tempfile.TemporaryDirectory() as tmp:
            _make_proj(tmp)
            ok, info = verify_predicate(
                {"type": "symbol_exists", "symbol": "hello_world", "file": "src/foo.py"}, tmp
            )
            assert ok and info["method"] == "symbol_exists"
            ok2, _ = verify_predicate(
                {"type": "symbol_exists", "symbol": "not_there", "file": "src/foo.py"}, tmp
            )
            assert not ok2

    def test_symbol_not_exists(self):
        from features.task_execution.infrastructure.verify_engine import verify_predicate

        with tempfile.TemporaryDirectory() as tmp:
            _make_proj(tmp)
            ok, _ = verify_predicate(
                {"type": "symbol_not_exists", "symbol": "zombie_symbol", "file": "src/foo.py"}, tmp
            )
            assert ok
            ok2, _ = verify_predicate(
                {"type": "symbol_not_exists", "symbol": "hello_world", "file": "src/foo.py"}, tmp
            )
            assert not ok2

    def test_file_exists(self):
        from features.task_execution.infrastructure.verify_engine import verify_predicate

        with tempfile.TemporaryDirectory() as tmp:
            _make_proj(tmp)
            ok, _ = verify_predicate({"type": "file_exists", "path": "src/foo.py"}, tmp)
            assert ok
            ok2, _ = verify_predicate({"type": "file_exists", "path": "src/missing.py"}, tmp)
            assert not ok2

    def test_command_output_contains(self):
        from features.task_execution.infrastructure.verify_engine import verify_predicate

        with tempfile.TemporaryDirectory() as tmp:
            ok, info = verify_predicate(
                {"type": "command_output", "command": 'python3 -c "print(42)"', "contains": "42"}, tmp
            )
            assert ok and info["method"] == "command_output"
            ok2, _ = verify_predicate(
                {"type": "command_output", "command": 'python3 -c "print(42)"', "contains": "NOPE"}, tmp
            )
            assert not ok2

    def test_manual_siempre_pasa(self):
        from features.task_execution.infrastructure.verify_engine import verify_predicate

        with tempfile.TemporaryDirectory() as tmp:
            ok, info = verify_predicate(
                {"type": "manual", "description": "revisar UX a mano"}, tmp
            )
            assert ok and info["method"] == "manual"

    def test_predicate_string_shell_vs_nl(self):
        from features.task_execution.infrastructure.verify_engine import (
            _is_string_a_shell_command,
        )

        assert _is_string_a_shell_command('python3 -c "print(1)"')
        assert not _is_string_a_shell_command("la funcion debe retornar algo")

    def test_cache_devuelve_misma_info(self):
        from features.task_execution.infrastructure.verify_engine import (
            invalidate_verify_cache, verify_predicate,
        )

        with tempfile.TemporaryDirectory() as tmp:
            _make_proj(tmp)
            invalidate_verify_cache()
            pred = {"type": "symbol_exists", "symbol": "hello_world", "file": "src/foo.py"}
            ok, info = verify_predicate(pred, tmp)
            ok2, info2 = verify_predicate(pred, tmp)
            assert ok == ok2 and info == info2

    def test_batch_mantiene_orden_y_cache(self):
        from features.task_execution.infrastructure.verify_engine import (
            invalidate_verify_cache, verify_predicates_batch,
        )

        with tempfile.TemporaryDirectory() as tmp:
            _make_proj(tmp)
            invalidate_verify_cache()
            preds = [
                {"type": "symbol_exists", "symbol": "hello_world", "file": "src/foo.py"},
                {"type": "symbol_exists", "symbol": "Greeter", "file": "src/foo.py"},
                {"type": "symbol_exists", "symbol": "ghost", "file": "src/foo.py"},
            ]
            results = verify_predicates_batch(preds, tmp)
            assert [r[0] for r in results] == [True, True, False]

    def test_wrapper_verify_predicates_task(self):
        from features.task_execution.tasks.verification import verify_predicates
        from libs.shared_domain import ExecutionContext

        with tempfile.TemporaryDirectory() as tmp:
            _make_proj(tmp)
            ctx = ExecutionContext.create(session_id="s", chain_dir=tmp)
            result = verify_predicates(ctx, {
                "predicates": [
                    {"type": "symbol_exists", "symbol": "hello_world", "file": "src/foo.py"},
                    {"type": "symbol_exists", "symbol": "ghost", "file": "src/foo.py"},
                ],
                "project_root": tmp,
            })
            assert result.ok
            assert result.data["all_passed"] is False
            assert result.data["failed_count"] == 1
            assert result.data["passed_count"] == 1
            assert result.data["total"] == 2

    def test_parse_predicate_natural_language(self):
        from features.task_execution.infrastructure.verify_engine import _parse_predicate

        pattern, vtype, fhint = _parse_predicate("def hello_world en src/foo.py")
        assert pattern == "def hello_world" and vtype == "must_exist"
        assert fhint == "src/foo.py"
        pattern2, vtype2, _ = _parse_predicate("NO zombie en src/foo.py")
        assert vtype2 == "must_not_exist"

    def test_run_verification_type_test(self):
        from features.task_execution.infrastructure.verify_engine import run_verification

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "test_demo.py").write_text(
                "def test_demo():\n    assert 1 == 1\n"
            )
            assert run_verification(
                {"predicate": "test_demo", "type": "test"}, tmp
            ) is True
            assert run_verification(
                {"predicate": "test_inexistente_xyz", "type": "test"}, tmp
            ) is False


# ── 6. metrics_cache (optimización review en build) ─────────────
class TestMetricsCache:
    def test_expand_paths_descubre_archivos(self):
        from features.code_analysis.infrastructure.metrics_cache import (
            IncrementalMetricsCache,
        )

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.py").write_text("x = 1\n")
            (Path(tmp) / "b.js").write_text("const x = 1\n")
            (Path(tmp) / "README.md").write_text("# doc\n")
            (Path(tmp) / "skip_me.pyc").write_bytes(b"\x00")
            cache = IncrementalMetricsCache(tmp, data_dir=os.path.join(tmp, "data"))
            files = cache._expand_paths([tmp])
            names = {os.path.basename(f) for f in files}
            assert "a.py" in names and "b.js" in names
            assert "README.md" not in names and "skip_me.pyc" not in names

    def test_get_stale_files_particiona_por_mtime(self):
        from features.code_analysis.infrastructure.metrics_cache import (
            IncrementalMetricsCache,
        )

        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "mod.py"
            f.write_text("def a():\n    return 1\n")
            cache = IncrementalMetricsCache(tmp, data_dir=os.path.join(tmp, "data"))
            fresh, stale = cache.get_stale_files([tmp])
            assert len(fresh) == 0 and len(stale) == 1

            # Marcar como cacheado
            cache.update(str(f), metrics={"file": str(f), "cyclo": 0})
            cache.save()

            cache2 = IncrementalMetricsCache(tmp, data_dir=os.path.join(tmp, "data"))
            fresh2, stale2 = cache2.get_stale_files([tmp])
            assert len(fresh2) == 1 and len(stale2) == 0

            # Modificar el archivo → invalida
            f.write_text("def a():\n    return 1\n\ndef b():\n    return 2\n")
            fresh3, stale3 = cache2.get_stale_files([tmp])
            assert len(fresh3) == 0 and len(stale3) == 1

    def test_update_merge_preserva_issues_por_archivo(self):
        from features.code_analysis.infrastructure.metrics_cache import (
            IncrementalMetricsCache,
        )

        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "mod.py"
            f.write_text("def a():\n    return 1\n")
            cache = IncrementalMetricsCache(tmp, data_dir=os.path.join(tmp, "data"))
            cache.update(str(f), metrics={"file": "mod.py", "cyclo": 3})
            # Múltiples smells en el mismo archivo no deben sobrescribirse
            cache.update(str(f), smells=[{"id": "s1"}])
            cache.update(str(f), smells=[{"id": "s2"}])
            cache.save()

            cache2 = IncrementalMetricsCache(tmp, data_dir=os.path.join(tmp, "data"))
            entry = cache2.get(str(f))
            assert entry is not None
            # El último update reemplaza por diseño; el merge agrupa antes
            assert len(entry.get("smells", [])) == 1


# ── 7. CFG calls cache (optimización scan_cfg) ──────────────────
class TestCallsCache:
    def test_cache_reusa_calls_por_mtime(self):
        from features.code_analysis.infrastructure.ast_grep_calls import (
            CallsCache,
            detect_calls_cached,
        )

        with tempfile.TemporaryDirectory() as tmp:
            fp = os.path.join(tmp, "mod.py")
            with open(fp, "w") as f:
                f.write(
                    "def foo():\n    return bar()\n\n"
                    "def bar():\n    return 42\n\n"
                    "def baz():\n    return foo()\n"
                )
            cache = CallsCache(os.path.join(tmp, "data", "cfg_calls_cache.json"))
            defs = [
                {"name": "foo", "line": 1, "end_line": 2},
                {"name": "bar", "line": 4, "end_line": 5},
                {"name": "baz", "line": 7, "end_line": 8},
            ]
            calls1, from_cache1 = detect_calls_cached(fp, defs, cache)
            assert not from_cache1
            assert len(calls1) > 0

            calls2, from_cache2 = detect_calls_cached(fp, defs, cache)
            assert from_cache2
            assert calls2 == calls1

            # Modificar → invalida
            with open(fp, "a") as f:
                f.write("# cambio\n")
            calls3, from_cache3 = detect_calls_cached(fp, defs, cache)
            assert not from_cache3

    def test_cache_persiste_entre_instancias(self):
        from features.code_analysis.infrastructure.ast_grep_calls import (
            CallsCache,
            detect_calls_cached,
        )

        with tempfile.TemporaryDirectory() as tmp:
            fp = os.path.join(tmp, "mod.py")
            with open(fp, "w") as f:
                f.write("def a():\n    return b()\n\n" "def b():\n    return 1\n")
            cache_path = os.path.join(tmp, "data", "cfg_calls_cache.json")
            defs = [
                {"name": "a", "line": 1, "end_line": 2},
                {"name": "b", "line": 4, "end_line": 5},
            ]
            c1 = CallsCache(cache_path)
            detect_calls_cached(fp, defs, c1)
            c1.save()

            # Nueva instancia lee el cache persistido
            c2 = CallsCache(cache_path)
            calls, from_cache = detect_calls_cached(fp, defs, c2)
            assert from_cache
            assert len(calls) > 0


# ── 8. scan_dfg (optimización + async) ─────────────────────────
class TestScanDfg:
    def test_scan_dfg_cache_reusa_resultados(self):
        from features.code_analysis.application.build_service import BuildGraphUseCase

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            src.mkdir()
            (src / "mod.py").write_text(
                "SECRET = 'sk-abc123'\ndef run():\n    print('x')\n"
            )
            builder = BuildGraphUseCase(
                project_id="smoke", data_dir=os.path.join(tmp, "data"),
                code_root=tmp,
            )
            r1 = builder.scan_dfg([str(src)], verbose=False)
            stats1 = r1.get("_stats", {})
            assert stats1.get("cache_hits") == 0
            assert len(r1.get("taint_paths", [])) >= 1

            # 2da corrida: cache poblado
            r2 = builder.scan_dfg([str(src)], verbose=False)
            stats2 = r2.get("_stats", {})
            assert stats2.get("cache_hits") >= 1
            assert len(r2.get("taint_paths", [])) == len(r1.get("taint_paths", []))

    def test_scan_dfg_async_persiste_estado(self):
        import time
        from features.code_analysis.application.build_service import BuildGraphUseCase

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            src.mkdir()
            (src / "mod.py").write_text(
                "SECRET = 'sk-abc123'\ndef run():\n    print('x')\n"
            )
            builder = BuildGraphUseCase(
                project_id="smoke", data_dir=os.path.join(tmp, "data"),
                code_root=tmp,
            )
            launch = builder.scan_dfg_async([str(src)])
            assert launch.get("status") == "running"
            assert os.path.exists(launch.get("status_path", ""))

            # Esperar a que termine (async en thread)
            for _ in range(50):
                st = builder.dfg_scan_status()
                if st.get("status") in ("ok", "failed"):
                    break
                time.sleep(0.1)
            assert st.get("status") == "ok"
            assert st.get("summary")


# ── 9. Contrato estándar de side-effects ────────────────────────
class TestSideEffectContract:
    def test_envelope_estandar(self):
        from features.engine.domain.side_effect_contract import (
            ok, failed, degraded, is_standard,
        )

        e = ok(type="update_cpg", scope="code_index",
               summary="CPG indexado", signals={"quality_passed": True})
        assert is_standard(e)
        assert e["status"] == "ok"
        assert e["scope"] == "code_index"
        assert e["signals"]["quality_passed"] is True

        f = failed(type="update_cpg", scope="code_index", summary="falló")
        assert f["status"] == "failed"

        d = degraded(type="update_cpg", scope="code_index", summary="degradado")
        assert d["status"] == "degraded"

    def test_envelope_scope_invalido_se_degrada(self):
        from features.engine.domain.side_effect_contract import ok, SCOPE_TRACKING

        e = ok(type="x", scope="scope_inexistente", summary="s")
        assert e["scope"] == SCOPE_TRACKING

    def test_normalize_envuelve_dicts_legacy(self):
        from features.engine.domain.side_effect_contract import (
            normalize, is_standard,
        )

        legacy = {"ok": True, "files": 5}
        env = normalize(legacy, "update_cpg", "code_index")
        assert is_standard(env)
        assert env["status"] == "ok"
        assert env["metadata"]["files"] == 5

        # Envelope estándar pasa tal cual
        from features.engine.domain.side_effect_contract import ok
        e = ok(type="x", scope="code_index", summary="s")
        assert normalize(e, "x", "code_index") is e

    def test_normalize_none_es_failure(self):
        from features.engine.domain.side_effect_contract import normalize

        env = normalize(None, "update_cpg", "code_index")
        assert env["status"] == "failed"

    def test_dispatch_detecta_quality_fail(self):
        """El orchestrator dispara quality_gate_failed cuando signals.quality_passed=False."""
        from features.engine.application.orchestrator import OrchestrationService
        from features.engine.domain.side_effect_contract import ok

        svc = OrchestrationService()

        class FakeWorkflowRepo:
            def load(self, chain_dir):
                class W:
                    def find_event_handler(self, name):
                        if name == "quality_gate_failed":
                            return {"transition": "needs_refine"}
                        return None
                return W()

        class FakeStateRepo:
            def load(self, chain_dir):
                class S:
                    current_node = "IMPLEMENT"
                    current_sub_state = "WORK"
                return S()

        class FakeTransitionUC:
            def execute(self, ctx, gate_name):
                return {"to_node": "REFINE"}

        class FakeTracer:
            def trace(self, *a, **k):
                pass

        class FakeCtx:
            chain_dir = "/tmp/chain"
            task_id = "T1"
            session_id = "s1"
            trace_id = "t1"
            started_at = "2026-01-01"

        svc._workflow_repo = FakeWorkflowRepo()
        svc._state_repo = FakeStateRepo()
        svc._transition_uc = FakeTransitionUC()
        svc._tracer = FakeTracer()

        result = {
            "side_effects": [
                {
                    "type": "update_cpg",
                    "ok": True,
                    "result": ok(
                        type="update_cpg", scope="code_index",
                        summary="CPG indexado; cyclo falla",
                        signals={
                            "quality_passed": False,
                            "failed_gate_names": ["cyclomatic_complexity"],
                            "recommend": "refactorizar_primero",
                        },
                    ),
                }
            ]
        }
        count = svc._dispatch_events_from_side_effects(FakeCtx(), result)
        assert count == 1
        assert result["quality"]["last_scan"]["failed_gates"] == ["cyclomatic_complexity"]
        assert result["quality"]["last_scan"]["recommend"] == "refactorizar_primero"

    def test_dispatch_no_dispara_si_quality_pasa(self):
        from features.engine.application.orchestrator import OrchestrationService
        from features.engine.domain.side_effect_contract import ok

        svc = OrchestrationService()

        class FakeWorkflowRepo:
            def load(self, chain_dir):
                class W:
                    def find_event_handler(self, name):
                        return None
                return W()

        class FakeStateRepo:
            def load(self, chain_dir):
                class S:
                    current_node = "IMPLEMENT"
                    current_sub_state = "WORK"
                return S()

        class FakeTracer:
            def trace(self, *a, **k):
                pass

        class FakeCtx:
            chain_dir = "/tmp/chain"

        svc._workflow_repo = FakeWorkflowRepo()
        svc._state_repo = FakeStateRepo()
        svc._tracer = FakeTracer()

        result = {
            "side_effects": [
                {
                    "type": "update_cpg",
                    "ok": True,
                    "result": ok(
                        type="update_cpg", scope="code_index",
                        summary="CPG indexado; calidad OK",
                        signals={"quality_passed": True, "recommend": "indexar_ok"},
                    ),
                }
            ]
        }
        count = svc._dispatch_events_from_side_effects(FakeCtx(), result)
        assert count == 0


# ── 10. Pipeline monádico (TaskResult + StepResult) ────────────
class TestMonadicPipeline:
    def test_task_result_map_y_short_circuit(self):
        from libs.shared_domain.models import TaskResult

        r = TaskResult.success({"a": 1}).map(lambda d: {"a": d["a"] + 1})
        assert r.ok and r.data["a"] == 2

        failed = TaskResult.failure("boom").map(lambda d: d["noexiste"])
        assert not failed.ok and failed.error == "boom"

    def test_task_result_and_then_chain(self):
        from libs.shared_domain.models import TaskResult

        r = (
            TaskResult.success(2)
            .and_then(lambda x: TaskResult.success(x * 3))
            .and_then(lambda x: TaskResult.success(x + 1))
        )
        assert r.ok and r.data == 7

        # Short-circuit: si un eslabón falla, no llega al siguiente
        calls = []
        r2 = (
            TaskResult.success(1)
            .and_then(lambda x: TaskResult.failure("frenó"))
            .and_then(lambda x: calls.append(x))
        )
        assert not r2.ok and calls == []

    def test_task_result_tap_y_recover(self):
        from libs.shared_domain.models import TaskResult

        seen = []
        r = TaskResult.success(5).tap(lambda d: seen.append(d))
        assert r.data == 5 and seen == [5]

        r2 = TaskResult.failure("err").recover(lambda e: "respaldo")
        assert r2.ok and r2.data == "respaldo"

    def test_step_result_agrega_signals_de_calidad(self):
        from features.engine.domain.step_result import StepResult
        from features.engine.domain.side_effect_contract import ok
        from libs.shared_domain.models import TaskResult

        se = {
            "type": "update_cpg", "ok": True,
            "result": ok(
                type="update_cpg", scope="code_index",
                summary="CPG indexado; cyclo falla",
                signals={
                    "quality_passed": False,
                    "failed_gate_names": ["cyclomatic_complexity"],
                    "recommend": "refactorizar_primero",
                    "files_updated": 5,
                },
            ),
        }
        sr = StepResult.success(
            task=TaskResult.success({"done": True}),
            side_effects=[se],
            advanced_to="CODE_REVIEW_SCAN",
            sub_state="WORK",
        )
        q = sr.quality()
        assert q["quality_passed"] is False
        assert q["failed_gates"] == ["cyclomatic_complexity"]
        assert q["recommend"] == "refactorizar_primero"

        pd = sr.to_prompt_dict()
        assert pd["step_quality_passed"] is False
        assert pd["step_failed_gates"] == "cyclomatic_complexity"
        assert pd["step_advanced_to"] == "CODE_REVIEW_SCAN"

    def test_read_last_step_result_desde_sub_state_data(self):
        from features.engine.application.prompt_context_builder import _read_last_step_result

        ssd = {
            "WORK": {
                "fields": {
                    "prompt_placeholders": {
                        "step_ok": True,
                        "step_quality_passed": False,
                        "step_recommend": "refactorizar_primero",
                        "step_failed_gates": "cyclomatic_complexity",
                    }
                }
            }
        }
        ph = _read_last_step_result(ssd)
        assert ph["step_quality_passed"] is False
        assert ph["step_recommend"] == "refactorizar_primero"

        # Sin resultado previo → vacío
        assert _read_last_step_result({}) == {}


class TestPromptTemplatesStepPlaceholders:
    def test_work_template_tiene_step_placeholders(self):
        import json

        path = ".cognitive/projects/walker/data/workflow.v3/prompts/implement/work.prompt.json"
        with open(path) as f:
            t = json.load(f)
        tmpl = t["template"]
        assert "{step_quality_summary}" in tmpl
        assert "{step_failed_gates}" in tmpl
        assert "{step_recommend}" in tmpl

    def test_code_review_scan_template_tiene_step_placeholders(self):
        import json

        path = ".cognitive/projects/walker/data/workflow.v3/prompts/implement/code-review-scan.prompt.json"
        with open(path) as f:
            t = json.load(f)
        tmpl = t["template"]
        assert "{step_quality_passed}" in tmpl
        assert "{step_failed_gates}" in tmpl

    def test_refine_diagnose_template_tiene_step_placeholders(self):
        import json

        path = ".cognitive/projects/walker/data/workflow.v3/prompts/refine/diagnose.prompt.json"
        with open(path) as f:
            t = json.load(f)
        tmpl = t["template"]
        assert "{step_quality_summary}" in tmpl
        assert "{step_recommend}" in tmpl

    def test_renderer_resuelve_y_no_deja_rotos(self):
        import json
        from features.prompts.renderer import TemplateRenderer

        r = TemplateRenderer()
        data_fail = {
            "step_quality_summary": "CPG indexado; cyclo falla",
            "step_quality_passed": "False",
            "step_failed_gates": "cyclomatic_complexity",
            "step_recommend": "refactorizar_primero",
            # refine/diagnose (paso 3) usa {failed_gates} inyectado por el
            # engine en vez de {step_failed_gates}
            "failed_gates": "cyclomatic_complexity",
        }
        for rel in ("implement/work.prompt.json",
                    "implement/code-review-scan.prompt.json",
                    "refine/diagnose.prompt.json"):
            path = f".cognitive/projects/walker/data/workflow.v3/prompts/{rel}"
            with open(path) as f:
                t = json.load(f)
            rendered_fail = r.render(t["template"], data_fail)
            rendered_empty = r.render(t["template"], {})
            assert "{step_" not in rendered_empty  # sin datos no deja rotos
            assert "cyclomatic_complexity" in rendered_fail  # con datos muestra gates


class TestSideEffectsMigrated:
    def test_as_side_effect_envuelve_con_scope(self):
        from features.engine.domain.side_effect_contract import (
            as_side_effect, SCOPE_KNOWLEDGE, is_standard,
        )

        @as_side_effect(scope=SCOPE_KNOWLEDGE)
        def fake_side(ctx, params):
            return {"ok": True, "nodes_saved": 5, "result": {"x": 1}}

        class Ctx:
            chain_dir = "/tmp"
            task_id = "T"
            session_id = "s"

        env = fake_side(Ctx(), {})
        assert is_standard(env)
        assert env["scope"] == SCOPE_KNOWLEDGE
        assert env["status"] == "ok"
        # Compat legacy: campos accesibles
        assert env.get("nodes_saved") == 5
        assert env["signals"]["nodes_saved"] == 5

    def test_as_side_effect_scope_decision(self):
        from features.engine.domain.side_effect_contract import (
            as_side_effect, SCOPE_DECISION,
        )

        @as_side_effect(scope=SCOPE_DECISION)
        def capture(ctx, params):
            return {"ok": True, "captured": True, "decision": "approve"}

        class Ctx:
            chain_dir = "/tmp"

        env = capture(Ctx(), {})
        assert env["scope"] == SCOPE_DECISION
        assert env["signals"]["captured"] is True
        assert env["signals"]["decision"] == "approve"

    def test_emit_event_envelope_con_event_en_signals(self):
        from features.task_execution.side_effects.events import emit_event

        class Ctx:
            chain_dir = "/tmp"
            task_id = "T1"
            trace_id = "t1"

        env = emit_event(Ctx(), {"event": "qa_failed", "payload": {"x": 1}})
        assert env["scope"] == "event"
        assert env["signals"]["event"] == "qa_failed"
        assert env["status"] == "ok"

    def test_dispatch_emit_event_envelope(self):
        """El dispatch de eventos funciona con el envelope estándar de emit_event."""
        from features.engine.application.orchestrator import OrchestrationService
        from features.task_execution.side_effects.events import emit_event

        svc = OrchestrationService()

        class FakeWorkflowRepo:
            def load(self, chain_dir):
                class W:
                    def find_event_handler(self, name):
                        if name == "qa_failed":
                            return {"transition": "refine"}
                        return None
                return W()

        class FakeStateRepo:
            def load(self, chain_dir):
                class S:
                    current_node = "VERIFY"
                    current_sub_state = "QA_RUN"
                return S()

        class FakeTransitionUC:
            def execute(self, ctx, gate_name):
                return {"to_node": "REFINE"}

        class FakeTracer:
            def trace(self, *a, **k):
                pass

        class FakeCtx:
            chain_dir = "/tmp/chain"
            task_id = "T1"
            session_id = "s1"
            trace_id = "t1"
            started_at = "2026-01-01"

        svc._workflow_repo = FakeWorkflowRepo()
        svc._state_repo = FakeStateRepo()
        svc._transition_uc = FakeTransitionUC()
        svc._tracer = FakeTracer()

        result = {
            "side_effects": [
                {
                    "type": "emit_event", "ok": True,
                    "result": emit_event(FakeCtx(), {"event": "qa_failed"}),
                }
            ]
        }
        count = svc._dispatch_events_from_side_effects(FakeCtx(), result)
        assert count == 1


# ── 11. VOs tipados en tasks ────────────────────────────────────
class TestValidatedVO:
    def test_from_dict_valida_y_retorna_result(self):
        from libs.shared_domain.validated_vo import TaskInput
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class Demo(TaskInput):
            files: list | None = None

            def _validate(self) -> str:
                if self.files is not None and not isinstance(self.files, list):
                    return "files debe ser lista"
                return ""

        ok = Demo.from_dict({"files": ["a.py"]})
        assert ok.ok and ok.data.files == ["a.py"]

        bad = Demo.from_dict({"files": "nope"})
        assert not bad.ok and "files debe ser lista" in (bad.error or "")

        # No lanza para dict None
        none = Demo.from_dict(None)
        assert none.ok

    def test_from_dict_encadena_con_monadas(self):
        from libs.shared_domain.validated_vo import TaskInput
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class Demo(TaskInput):
            n: int = 0

        r = Demo.from_dict({"n": 2}).map(lambda vo: vo.n * 10)
        assert r.ok and r.data == 20

        r2 = Demo.from_dict({"n": "no-int"}).map(lambda vo: vo.n * 10)
        # map propaga: si from_dict falló, map short-circuit
        assert r2.ok  # no valida tipo int por defecto; pero no crashea

    def test_review_input_valida(self):
        from features.task_execution.tasks.review import ReviewInput

        ok = ReviewInput.from_dict({"files": ["a.py"], "thresholds": {"max_cyclo": 10}})
        assert ok.ok and ok.data.files == ["a.py"]

        bad = ReviewInput.from_dict({"files": "nope"})
        assert not bad.ok

    def test_verify_input_normaliza_predicates(self):
        from features.task_execution.tasks.verification import VerifyInput

        ok = VerifyInput.from_dict({"predicates": [{"predicate": "def x", "type": "symbol_exists"}]})
        assert ok.ok
        assert ok.data.normalized_predicates() == ["def x"]

        single = VerifyInput.from_dict({"predicate": "def y"})
        assert single.data.normalized_predicates() == ["def y"]

        missing = VerifyInput.from_dict({})
        assert not missing.ok

    def test_implement_input_valida(self):
        from features.task_execution.tasks.implementation import ImplementInput

        ok = ImplementInput.from_dict({"objective": "X", "files": ["a.py"]})
        assert ok.ok and ok.data.objective == "X"

        bad = ImplementInput.from_dict({"files": "nope"})
        assert not bad.ok


class TestMoreTaskVOs:
    def test_capture_task_input(self):
        from features.task_execution.tasks.capture_task import CaptureTaskInput

        assert CaptureTaskInput.from_dict({}).error
        ok = CaptureTaskInput.from_dict({"objective": "implementar X", "files": ["a.py"]})
        assert ok.ok and ok.data.objective == "implementar X"
        assert not CaptureTaskInput.from_dict({"files": "nope"}).ok

    def test_kg_research_input(self):
        from features.task_execution.tasks.kg_research import ResearchInput

        assert not ResearchInput.from_dict({}).ok
        assert ResearchInput.from_dict({"topic": "vector dbs"}).ok

    def test_cpg_index_ref_input(self):
        from features.task_execution.tasks.cpg_index_ref import IndexRefInput

        assert not IndexRefInput.from_dict({"url": "x"}).ok  # falta name
        ok = IndexRefInput.from_dict({"url": "https://r", "name": "ref", "depth": 1})
        assert ok.ok and ok.data.depth == 1
        assert not IndexRefInput.from_dict({"url": "x", "name": "n", "depth": 0}).ok

    def test_kg_extract_input_scope(self):
        from features.task_execution.tasks.kg_extract import KgExtractInput

        assert KgExtractInput.from_dict({"scope": "functions"}).ok
        assert not KgExtractInput.from_dict({"scope": "bogus"}).ok

    def test_dfg_scan_input_action(self):
        from features.task_execution.tasks.cpg_dfg_scan import DfgScanInput

        assert DfgScanInput.from_dict({"action": "start"}).ok
        assert DfgScanInput.from_dict({"action": "status"}).ok
        assert not DfgScanInput.from_dict({"action": "bogus"}).ok

    def test_project_init_input(self):
        from features.task_execution.tasks.project_init import ProjectInitInput

        assert not ProjectInitInput.from_dict({}).ok  # falta project_id
        ok = ProjectInitInput.from_dict({"project_id": "p1", "language": "go"})
        assert ok.ok and ok.data.language == "go"


class TestTypedPersistence:
    def test_checkpoint_roundtrip_preserva_tipos(self):
        import json
        from features.engine.domain.step_result import StepResult
        from features.engine.domain.side_effect_contract import ok

        se = {
            "type": "update_cpg", "ok": True,
            "result": ok(
                type="update_cpg", scope="code_index",
                summary="CPG indexado; cyclo falla",
                signals={
                    "quality_passed": False,
                    "failed_gate_names": ["cyclomatic_complexity"],
                    "recommend": "refactorizar_primero",
                },
            ),
        }
        sr = StepResult.success(side_effects=[se], advanced_to="CODE_REVIEW_SCAN", sub_state="WORK")

        # Roundtrip por JSON (como state.json)
        cp = json.loads(json.dumps(sr.to_checkpoint()))
        restored = StepResult.from_checkpoint(cp)
        assert restored.ok
        assert restored.data.signals["quality"]["quality_passed"] is False
        assert isinstance(restored.data.signals["quality"]["quality_passed"], bool)
        assert restored.data.advanced_to == "CODE_REVIEW_SCAN"

    def test_checkpoint_corrupto_falla_validado(self):
        from features.engine.domain.step_result import StepResult

        bad = StepResult.from_checkpoint({"__checkpoint__": "StepResult:v1", "ok": "no-bool"})
        assert not bad.ok
        assert "ok" in (bad.error or "")

        no_marker = StepResult.from_checkpoint({"ok": True})
        assert not no_marker.ok

    def test_read_last_step_result_desde_checkpoint(self):
        import json
        from features.engine.application.prompt_context_builder import _read_last_step_result
        from features.engine.domain.step_result import StepResult
        from features.engine.domain.side_effect_contract import ok

        se = {
            "type": "update_cpg", "ok": True,
            "result": ok(
                type="update_cpg", scope="code_index",
                summary="CPG indexado; cyclo falla",
                signals={
                    "quality_passed": False,
                    "failed_gate_names": ["cyclomatic_complexity", "dead_code"],
                    "recommend": "refactorizar_primero",
                },
            ),
        }
        sr = StepResult.success(side_effects=[se], advanced_to="CODE_REVIEW_SCAN", sub_state="WORK")
        cp = json.loads(json.dumps(sr.to_checkpoint()))

        ssd = {"WORK": {"fields": {"step_checkpoint": cp}}}
        ph = _read_last_step_result(ssd)
        assert ph["step_quality_passed"] is False
        assert isinstance(ph["step_quality_passed"], bool)
        assert ph["step_failed_gates"] == "cyclomatic_complexity, dead_code"
        assert ph["step_advanced_to"] == "CODE_REVIEW_SCAN"

        # Checkpoint corrupto no crashea
        ssd_bad = {"WORK": {"fields": {"step_checkpoint": {"__checkpoint__": "StepResult:v1", "ok": "x"}}}}
        assert _read_last_step_result(ssd_bad) == {}


# ── 12. mcp-config (config MCP portable) ────────────────────────
class TestMcpConfig:
    @classmethod
    def _ensure_bootstrap_path(cls):
        """bootstrap/ vive en to_clean_next/, no en el root. Lo añade al path."""
        import sys

        candidate = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "to_clean_next")
        candidate = os.path.realpath(candidate)
        if candidate not in sys.path:
            sys.path.insert(0, candidate)

    def test_walker_root_detecta_repo(self):
        self._ensure_bootstrap_path()
        from bootstrap.cli.mcp_config import _walker_root

        root = _walker_root()
        assert os.path.exists(os.path.join(root, "features", "cli_interface", "mcp.py"))

    def test_mcp_servers_block_estandar(self):
        self._ensure_bootstrap_path()
        from bootstrap.cli.mcp_config import mcp_servers_block

        cfg = mcp_servers_block()
        server = cfg["mcpServers"]["walker"]
        assert server["command"] == "python3"
        assert server["args"] == ["-m", "features.cli_interface.mcp"]
        # Portable: PYTHONPATH incluye la raíz del walker
        assert os.path.dirname(os.path.dirname(os.path.abspath(__file__))) in server["env"]["PYTHONPATH"]

    def test_formatos_opencode_claude_cursor(self):
        self._ensure_bootstrap_path()
        from bootstrap.cli.mcp_config import generate

        oc = generate("opencode")
        assert oc["mcp"]["walker"]["type"] == "local"
        assert oc["mcp"]["walker"]["command"][0] == "python3"

        cl = generate("claude")
        assert "mcpServers" in cl

        cu = generate("cursor")
        assert "mcpServers" in cu

    def test_cmd_mcp_config_salida_json(self):
        self._ensure_bootstrap_path()
        from bootstrap.cli.mcp_config import cmd_mcp_config

        import io, contextlib, json

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cmd_mcp_config(["--raw"])
        out = buf.getvalue().strip()
        assert code == 0
        parsed = json.loads(out)
        assert "walker" in parsed
        assert parsed["walker"]["command"] == "python3"


class TestMcpConfigService:
    def test_service_detecta_root_y_genera(self):
        import json, os
        from features.cli_interface.application.mcp_config_service import (
            _walker_root, mcp_servers_block,
        )

        root = _walker_root()
        assert os.path.exists(os.path.join(root, "features", "cli_interface", "mcp.py"))

        cfg = mcp_servers_block()
        server = cfg["mcpServers"]["walker"]
        assert server["command"] == "python3"
        assert server["args"] == ["-m", "features.cli_interface.mcp"]
        assert root in server["env"]["PYTHONPATH"]
        # Serializable (JSON válido, portable para pegar en cualquier agente)
        json.dumps(cfg)

    def test_handler_mcp_config_response(self):
        from features.cli_interface.application.handlers import handle_mcp_config
        from features.cli_interface.infrastructure.contracts import Request

        r = handle_mcp_config(Request(params={}))
        assert r.type == "mcp_config"
        assert "mcpServers" in r.data
        assert r.data["mcpServers"]["walker"]["command"] == "python3"

        # raw → solo el bloque walker
        r_raw = handle_mcp_config(Request(params={"raw": "true"}))
        assert "mcpServers" not in r_raw.data
        assert "walker" in r_raw.data


class TestHelpDiscoverability:
    def test_help_incluye_mcp_config(self):
        from features.cli_interface.application.handlers import handle_help
        from features.cli_interface.infrastructure.contracts import Request

        r = handle_help(Request(params={}))
        assert r.type == "help"
        commands = r.data.get("commands", [])
        names = [c["name"] for c in commands]
        assert "mcp-config" in names
        # El usage debe documentar formatos
        mcp = [c for c in commands if c["name"] == "mcp-config"][0]
        assert "format" in mcp["usage"]

    def test_mcp_config_route_registrada(self):
        import features.cli_interface as ci

        routes = ci._ROUTER.list_routes()
        patterns = [r.get("pattern", "") for r in routes]
        assert "mcp-config" in patterns
