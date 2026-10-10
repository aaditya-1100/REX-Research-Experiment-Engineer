"""Empirical verification script to demonstrate edge cases and vulnerabilities in self_deception and verifier."""

from __future__ import annotations

from rex.domain.models import ClaimStatus, HypothesisStatus
from rex.evidence.self_deception import ScientificSelfDeceptionDetector
from rex.evidence.verifier import ResearchVerifier
from rex.observability.events import ActorType
from rex.persistence.database import (
    create_db_engine,
    create_session_factory,
    get_db_session,
    init_db,
)
from rex.persistence.models import (
    AnalysisModel,
    ClaimModel,
    EvidenceLinkModel,
    ExecutionModel,
    ExperimentModel,
    HypothesisModel,
    ResearchRunModel,
    ResultModel,
)


def test_detector_negative_delta_with_significant_p():
    detector = ScientificSelfDeceptionDetector(significance_threshold=0.05)

    class MockAnalysis:
        def __init__(self, output_json):
            self.output_json = output_json

    # Treatment is significantly WORSE: delta = -0.05, p = 0.001
    claim_stmt = "The proposed method improves performance significantly."
    analyses = [MockAnalysis({"metric_name": "accuracy", "delta": -0.05, "p_value": 0.001})]

    report = detector.audit_claim(
        claim_statement=claim_stmt,
        claim_metadata={"claim_type": "superiority"},
        analyses=analyses,
    )

    print("--- TEST 1: Detector on negative delta with significant p (p=0.001, delta=-0.05) ---")
    print(f"is_grounded: {report.is_grounded}")
    print(f"is_statistically_significant: {report.is_statistically_significant}")
    print(f"findings: {report.findings}")
    print(f"grounding_score: {report.grounding_score}")
    assert report.is_grounded is True, (
        "Confirmed: detector currently marks negative delta with p<0.05 as grounded!"
    )


def test_verifier_negative_delta_with_improves():
    engine = create_db_engine(database_url="sqlite:///:memory:")
    init_db(engine)
    session_factory = create_session_factory(engine)

    run_id = "run_demo_improves"
    with get_db_session(session_factory) as session:
        run = ResearchRunModel(id=run_id, research_question="Does treatment improve?")
        hyp = HypothesisModel(
            id=f"hyp_{run_id}",
            research_run_id=run_id,
            statement="Treatment improves performance.",
            status=HypothesisStatus.PROPOSED.value,
            falsification_condition="Delta <= 0",
        )
        exp = ExperimentModel(
            id=f"exp_{run_id}",
            research_run_id=run_id,
            hypothesis_id=hyp.id,
            objective="Evaluate treatment",
            specification_json={"name": "exp"},
        )
        exe = ExecutionModel(
            id=f"exec_{run_id}",
            experiment_id=exp.id,
            status="completed",
            exit_code=0,
            seed=42,
        )
        res = ResultModel(
            id=f"res_{run_id}",
            execution_id=exe.id,
            metric_name="accuracy",
            metric_value=0.80,
            result_json={"seed": 42},
        )
        an = AnalysisModel(
            id=f"an_{run_id}",
            research_run_id=run_id,
            analysis_type="welch_t_test",
            input_result_ids=[res.id],
            method="welch_t_test",
            output_json={
                "accuracy": 0.80,
                "delta": -0.08,  # Degraded by 8%!
                "p_value": 0.001,  # Significant degradation!
                "seeds": [42],
            },
        )
        # Using present-tense "improves"
        clm = ClaimModel(
            id=f"clm_{run_id}",
            research_run_id=run_id,
            statement="Treatment improves accuracy to 0.80.",
            status=ClaimStatus.PROPOSED.value,
            metadata_json={"metric_name": "accuracy", "asserted_value": 0.80},
        )

        lnk1 = EvidenceLinkModel(
            source_type="claim",
            source_id=clm.id,
            target_type="analysis",
            target_id=an.id,
            relationship_type="supported_by",
            research_run_id=run_id,
        )
        lnk2 = EvidenceLinkModel(
            source_type="analysis",
            source_id=an.id,
            target_type="result",
            target_id=res.id,
            relationship_type="derived_from",
            research_run_id=run_id,
        )
        lnk3 = EvidenceLinkModel(
            source_type="result",
            source_id=res.id,
            target_type="execution",
            target_id=exe.id,
            relationship_type="produced_by",
            research_run_id=run_id,
        )
        lnk4 = EvidenceLinkModel(
            source_type="execution",
            source_id=exe.id,
            target_type="experiment",
            target_id=exp.id,
            relationship_type="instance_of",
            research_run_id=run_id,
        )
        lnk_res = EvidenceLinkModel(
            source_type="claim",
            source_id=clm.id,
            target_type="result",
            target_id=res.id,
            relationship_type="supported_by",
            research_run_id=run_id,
        )

        session.add_all([run, hyp, exp, exe, res, an, clm, lnk1, lnk2, lnk3, lnk4, lnk_res])
        session.commit()

    with get_db_session(session_factory) as session:
        verifier = ResearchVerifier(session=session)
        report = verifier.verify_run(run_id, actor=ActorType.VERIFIER)

        clm = session.get(ClaimModel, f"clm_{run_id}")
        hyp = session.get(HypothesisModel, f"hyp_{run_id}")

        print("--- TEST 2: Verifier on claim with 'improves', delta = -0.08, p = 0.001 ---")
        print(f"report.status: {report.status}")
        print(f"report.errors: {report.errors}")
        print(f"clm.status: {clm.status}")
        print(f"hyp.status: {hyp.status}")


def test_verifier_negative_delta_with_improved():
    engine = create_db_engine(database_url="sqlite:///:memory:")
    init_db(engine)
    session_factory = create_session_factory(engine)

    run_id = "run_demo_improved"
    with get_db_session(session_factory) as session:
        run = ResearchRunModel(id=run_id, research_question="Does treatment improve?")
        hyp = HypothesisModel(
            id=f"hyp_{run_id}",
            research_run_id=run_id,
            statement="Treatment improved performance.",
            status=HypothesisStatus.PROPOSED.value,
            falsification_condition="Delta <= 0",
        )
        exp = ExperimentModel(
            id=f"exp_{run_id}",
            research_run_id=run_id,
            hypothesis_id=hyp.id,
            objective="Evaluate treatment",
            specification_json={"name": "exp"},
        )
        exe = ExecutionModel(
            id=f"exec_{run_id}",
            experiment_id=exp.id,
            status="completed",
            exit_code=0,
            seed=42,
        )
        res = ResultModel(
            id=f"res_{run_id}",
            execution_id=exe.id,
            metric_name="accuracy",
            metric_value=0.80,
            result_json={"seed": 42},
        )
        an = AnalysisModel(
            id=f"an_{run_id}",
            research_run_id=run_id,
            analysis_type="welch_t_test",
            input_result_ids=[res.id],
            method="welch_t_test",
            output_json={
                "accuracy": 0.80,
                "delta": -0.08,  # Degraded by 8%!
                "p_value": 0.001,  # Significant degradation!
                "seeds": [42],
            },
        )
        # Using past-tense "improved"
        clm = ClaimModel(
            id=f"clm_{run_id}",
            research_run_id=run_id,
            statement="Treatment improved accuracy to 0.80.",
            status=ClaimStatus.PROPOSED.value,
            metadata_json={"metric_name": "accuracy", "asserted_value": 0.80},
        )

        lnk1 = EvidenceLinkModel(
            source_type="claim",
            source_id=clm.id,
            target_type="analysis",
            target_id=an.id,
            relationship_type="supported_by",
            research_run_id=run_id,
        )
        lnk2 = EvidenceLinkModel(
            source_type="analysis",
            source_id=an.id,
            target_type="result",
            target_id=res.id,
            relationship_type="derived_from",
            research_run_id=run_id,
        )
        lnk3 = EvidenceLinkModel(
            source_type="result",
            source_id=res.id,
            target_type="execution",
            target_id=exe.id,
            relationship_type="produced_by",
            research_run_id=run_id,
        )
        lnk4 = EvidenceLinkModel(
            source_type="execution",
            source_id=exe.id,
            target_type="experiment",
            target_id=exp.id,
            relationship_type="instance_of",
            research_run_id=run_id,
        )
        lnk_res = EvidenceLinkModel(
            source_type="claim",
            source_id=clm.id,
            target_type="result",
            target_id=res.id,
            relationship_type="supported_by",
            research_run_id=run_id,
        )

        session.add_all([run, hyp, exp, exe, res, an, clm, lnk1, lnk2, lnk3, lnk4, lnk_res])
        session.commit()

    with get_db_session(session_factory) as session:
        verifier = ResearchVerifier(session=session)
        report = verifier.verify_run(run_id, actor=ActorType.VERIFIER)

        clm = session.get(ClaimModel, f"clm_{run_id}")
        hyp = session.get(HypothesisModel, f"hyp_{run_id}")

        print("--- TEST 3: Verifier on claim with 'improved', delta = -0.08, p = 0.001 ---")
        print(f"report.status: {report.status}")
        print(f"report.errors: {report.errors}")
        print(f"clm.status: {clm.status}")
        print(f"hyp.status: {hyp.status}")


def test_verifier_cherry_picking_gap():
    engine = create_db_engine(database_url="sqlite:///:memory:")
    init_db(engine)
    session_factory = create_session_factory(engine)

    run_id = "run_demo_cherry"
    with get_db_session(session_factory) as session:
        run = ResearchRunModel(id=run_id, research_question="Does treatment improve?")
        hyp = HypothesisModel(
            id=f"hyp_{run_id}",
            research_run_id=run_id,
            statement="Treatment improves performance.",
            status=HypothesisStatus.PROPOSED.value,
            falsification_condition="Delta <= 0",
        )
        exp = ExperimentModel(
            id=f"exp_{run_id}",
            research_run_id=run_id,
            hypothesis_id=hyp.id,
            objective="Evaluate treatment",
            specification_json={"name": "exp"},
        )
        # 3 executions run in this experiment with seeds 10, 20, 30
        exe1 = ExecutionModel(
            id="exec_1", experiment_id=exp.id, status="completed", exit_code=0, seed=10
        )
        exe2 = ExecutionModel(
            id="exec_2", experiment_id=exp.id, status="completed", exit_code=0, seed=20
        )
        exe3 = ExecutionModel(
            id="exec_3", experiment_id=exp.id, status="completed", exit_code=0, seed=30
        )

        # 3 results produced
        res1 = ResultModel(
            id="res_1",
            execution_id=exe1.id,
            metric_name="accuracy",
            metric_value=0.95,
            result_json={"seed": 10},
        )
        res2 = ResultModel(
            id="res_2",
            execution_id=exe2.id,
            metric_name="accuracy",
            metric_value=0.50,
            result_json={"seed": 20},
        )
        res3 = ResultModel(
            id="res_3",
            execution_id=exe3.id,
            metric_name="accuracy",
            metric_value=0.45,
            result_json={"seed": 30},
        )

        # Analysis ONLY includes res1 (seed 10), cherry-picking the best run and omitting seeds 20 and 30
        an = AnalysisModel(
            id=f"an_{run_id}",
            research_run_id=run_id,
            analysis_type="welch_t_test",
            input_result_ids=[res1.id],  # ONLY res1
            method="welch_t_test",
            output_json={
                "accuracy": 0.95,
                "delta": 0.15,
                "p_value": 0.01,
                "seeds": [10],  # only reports seed 10
            },
        )
        clm = ClaimModel(
            id=f"clm_{run_id}",
            research_run_id=run_id,
            statement="Treatment achieved accuracy 0.95.",
            status=ClaimStatus.PROPOSED.value,
            metadata_json={"metric_name": "accuracy", "asserted_value": 0.95},
        )

        lnk1 = EvidenceLinkModel(
            source_type="claim",
            source_id=clm.id,
            target_type="analysis",
            target_id=an.id,
            relationship_type="supported_by",
            research_run_id=run_id,
        )
        lnk2 = EvidenceLinkModel(
            source_type="analysis",
            source_id=an.id,
            target_type="result",
            target_id=res1.id,
            relationship_type="derived_from",
            research_run_id=run_id,
        )
        lnk3 = EvidenceLinkModel(
            source_type="result",
            source_id=res1.id,
            target_type="execution",
            target_id=exe1.id,
            relationship_type="produced_by",
            research_run_id=run_id,
        )
        lnk4_1 = EvidenceLinkModel(
            source_type="execution",
            source_id=exe1.id,
            target_type="experiment",
            target_id=exp.id,
            relationship_type="instance_of",
            research_run_id=run_id,
        )
        lnk4_2 = EvidenceLinkModel(
            source_type="execution",
            source_id=exe2.id,
            target_type="experiment",
            target_id=exp.id,
            relationship_type="instance_of",
            research_run_id=run_id,
        )
        lnk4_3 = EvidenceLinkModel(
            source_type="execution",
            source_id=exe3.id,
            target_type="experiment",
            target_id=exp.id,
            relationship_type="instance_of",
            research_run_id=run_id,
        )
        lnk_res = EvidenceLinkModel(
            source_type="claim",
            source_id=clm.id,
            target_type="result",
            target_id=res1.id,
            relationship_type="supported_by",
            research_run_id=run_id,
        )

        session.add_all(
            [
                run,
                hyp,
                exp,
                exe1,
                exe2,
                exe3,
                res1,
                res2,
                res3,
                an,
                clm,
                lnk1,
                lnk2,
                lnk3,
                lnk4_1,
                lnk4_2,
                lnk4_3,
                lnk_res,
            ]
        )
        session.commit()

    with get_db_session(session_factory) as session:
        verifier = ResearchVerifier(session=session)
        report = verifier.verify_run(run_id, actor=ActorType.VERIFIER)

        clm = session.get(ClaimModel, f"clm_{run_id}")
        hyp = session.get(HypothesisModel, f"hyp_{run_id}")

        print("--- TEST 4: Verifier on cherry-picked seeds (ran 10, 20, 30; analyzed only 10) ---")
        print(f"report.status: {report.status}")
        print(f"report.errors: {report.errors}")
        print(f"clm.status: {clm.status}")
        print(f"hyp.status: {hyp.status}")


def test_verifier_no_p_value_statistically_significant():
    engine = create_db_engine(database_url="sqlite:///:memory:")
    init_db(engine)
    session_factory = create_session_factory(engine)

    run_id = "run_demo_no_p"
    with get_db_session(session_factory) as session:
        run = ResearchRunModel(id=run_id, research_question="Does treatment improve?")
        hyp = HypothesisModel(
            id=f"hyp_{run_id}",
            research_run_id=run_id,
            statement="Treatment improves performance.",
            status=HypothesisStatus.PROPOSED.value,
            falsification_condition="Delta <= 0",
        )
        exp = ExperimentModel(
            id=f"exp_{run_id}",
            research_run_id=run_id,
            hypothesis_id=hyp.id,
            objective="Evaluate treatment",
            specification_json={"name": "exp"},
        )
        exe = ExecutionModel(
            id="exec_1", experiment_id=exp.id, status="completed", exit_code=0, seed=42
        )
        res = ResultModel(
            id="res_1",
            execution_id=exe.id,
            metric_name="accuracy",
            metric_value=0.85,
            result_json={"seed": 42},
        )
        an = AnalysisModel(
            id=f"an_{run_id}",
            research_run_id=run_id,
            analysis_type="descriptive",
            input_result_ids=[res.id],
            method="summary_statistics",
            output_json={
                "accuracy": 0.85,
                "mean": 0.85,
                "seeds": [42],
            },
        )
        clm = ClaimModel(
            id=f"clm_{run_id}",
            research_run_id=run_id,
            statement="Treatment shows statistically significant improvement to 0.85.",
            status=ClaimStatus.PROPOSED.value,
            metadata_json={"metric_name": "accuracy", "asserted_value": 0.85},
        )

        lnk1 = EvidenceLinkModel(
            source_type="claim",
            source_id=clm.id,
            target_type="analysis",
            target_id=an.id,
            relationship_type="supported_by",
            research_run_id=run_id,
        )
        lnk2 = EvidenceLinkModel(
            source_type="analysis",
            source_id=an.id,
            target_type="result",
            target_id=res.id,
            relationship_type="derived_from",
            research_run_id=run_id,
        )
        lnk3 = EvidenceLinkModel(
            source_type="result",
            source_id=res.id,
            target_type="execution",
            target_id=exe.id,
            relationship_type="produced_by",
            research_run_id=run_id,
        )
        lnk4 = EvidenceLinkModel(
            source_type="execution",
            source_id=exe.id,
            target_type="experiment",
            target_id=exp.id,
            relationship_type="instance_of",
            research_run_id=run_id,
        )
        lnk_res = EvidenceLinkModel(
            source_type="claim",
            source_id=clm.id,
            target_type="result",
            target_id=res.id,
            relationship_type="supported_by",
            research_run_id=run_id,
        )

        session.add_all([run, hyp, exp, exe, res, an, clm, lnk1, lnk2, lnk3, lnk4, lnk_res])
        session.commit()

    with get_db_session(session_factory) as session:
        verifier = ResearchVerifier(session=session)
        report = verifier.verify_run(run_id, actor=ActorType.VERIFIER)

        clm = session.get(ClaimModel, f"clm_{run_id}")
        hyp = session.get(HypothesisModel, f"hyp_{run_id}")

        print(
            "--- TEST 5: Verifier on claim stating 'statistically significant' with no p-value ---"
        )
        print(f"report.status: {report.status}")
        print(f"report.errors: {report.errors}")
        print(f"clm.status: {clm.status}")
        print(f"hyp.status: {hyp.status}")


if __name__ == "__main__":
    test_detector_negative_delta_with_significant_p()
    test_verifier_negative_delta_with_improves()
    test_verifier_negative_delta_with_improved()
    test_verifier_cherry_picking_gap()
    test_verifier_no_p_value_statistically_significant()
