from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from atoms.core.errors import ProtocolError
from atoms.core.recovery.model import (
    DiagnosticIdentityRelation,
    HaltDiagnostic,
    JournalState,
    PersistentObservation,
    RollbackResult,
    ScratchObservation,
    ScratchRole,
    TransactionState,
)
from atoms.core.recovery.snapshot import RecoverySnapshot, TopologyNode


class PlanDisposition(Enum):
    ROLL_BACK = "roll_back"
    ROLL_BACK_REFUSED = "roll_back_refused"
    COMMITTED_CLEANUP = "committed_cleanup"
    DETACH_TERMINAL = "detach_terminal"
    HALT = "halt"
    NO_RECOVERY = "no_recovery"


class SettlementKind(Enum):
    RESTORE_PRE = "restore_pre"
    REMOVE_ATTRIBUTABLE_CREATION = "remove_attributable_creation"
    REPAIR_INTERMEDIATE = "repair_intermediate"
    FINISH_LANDED_UNDO = "finish_landed_undo"
    REMOVE_COMMITTED_SCRATCH = "remove_committed_scratch"


class EffectVariant(Enum):
    REPLACE_FILE = "replace_file"
    CREATE_FILE_NO_CLOBBER = "create_file_no_clobber"
    DELETE_PATH = "delete_path"
    MOVE_NO_CLOBBER = "move_no_clobber"
    CREATE_DIRECTORY = "create_directory"


@dataclass(frozen=True, slots=True)
class ParentOccupancy:
    parent: TopologyNode
    present_children: tuple[TopologyNode, ...]
    has_unmodeled_child: bool


@dataclass(frozen=True, slots=True)
class JointObservation:
    persistent: tuple[PersistentObservation, ...]
    scratch: tuple[ScratchObservation, ...]
    parent_occupancy: tuple[ParentOccupancy, ...]


@dataclass(frozen=True, slots=True)
class TransitionTransactionState:
    from_state: TransactionState
    to_state: TransactionState
    rollback_result: RollbackResult | None
    halt_diagnostic: HaltDiagnostic | None


@dataclass(frozen=True, slots=True)
class TransitionEffectState:
    effect_id: str
    from_state: JournalState
    to_state: JournalState


@dataclass(frozen=True, slots=True)
class TransformEffectTuple:
    effect_id: str
    variant: EffectVariant
    settlement: SettlementKind
    expected_before: JointObservation
    result_after: JointObservation
    identity_relations: tuple[DiagnosticIdentityRelation, ...]


@dataclass(frozen=True, slots=True)
class RemoveScratch:
    effect_id: str
    role: ScratchRole
    expected_before: JointObservation
    result_after: JointObservation


@dataclass(frozen=True, slots=True)
class PreserveExternal:
    nodes: tuple[TopologyNode, ...]


@dataclass(frozen=True, slots=True)
class DetachActive:
    pass


RecoveryStep = (
    TransitionTransactionState
    | TransitionEffectState
    | TransformEffectTuple
    | RemoveScratch
    | PreserveExternal
    | DetachActive
)

_PLAN_TOKEN = object()


@dataclass(frozen=True, slots=True, init=False)
class ActionPlan:
    bound_snapshot: RecoverySnapshot
    disposition: PlanDisposition
    steps: tuple[RecoveryStep, ...]
    rollback_result: RollbackResult | None

    def __init__(
        self,
        *,
        bound_snapshot: RecoverySnapshot,
        disposition: PlanDisposition,
        steps: tuple[RecoveryStep, ...],
        rollback_result: RollbackResult | None,
        _construction_token: object | None = None,
    ) -> None:
        if _construction_token is not _PLAN_TOKEN:
            raise TypeError("RecoveryPlan values are created only by classify_recovery")
        object.__setattr__(self, "bound_snapshot", bound_snapshot)
        object.__setattr__(self, "disposition", disposition)
        object.__setattr__(self, "steps", steps)
        object.__setattr__(self, "rollback_result", rollback_result)


@dataclass(frozen=True, slots=True, init=False)
class HaltPlan:
    bound_snapshot: RecoverySnapshot
    disposition: PlanDisposition = field(init=False)
    diagnostic: HaltDiagnostic
    steps: tuple[TransitionTransactionState, ...]

    def __init__(
        self,
        *,
        bound_snapshot: RecoverySnapshot,
        diagnostic: HaltDiagnostic,
        steps: tuple[TransitionTransactionState, ...],
        _construction_token: object | None = None,
    ) -> None:
        if _construction_token is not _PLAN_TOKEN:
            raise TypeError("RecoveryPlan values are created only by classify_recovery")
        object.__setattr__(self, "bound_snapshot", bound_snapshot)
        object.__setattr__(self, "disposition", PlanDisposition.HALT)
        object.__setattr__(self, "diagnostic", diagnostic)
        object.__setattr__(self, "steps", steps)


@dataclass(frozen=True, slots=True, init=False)
class NoRecoveryPlan:
    bound_snapshot: RecoverySnapshot
    disposition: PlanDisposition = field(init=False)
    steps: tuple[()] = field(init=False)

    def __init__(
        self,
        *,
        bound_snapshot: RecoverySnapshot,
        _construction_token: object | None = None,
    ) -> None:
        if _construction_token is not _PLAN_TOKEN:
            raise TypeError("RecoveryPlan values are created only by classify_recovery")
        object.__setattr__(self, "bound_snapshot", bound_snapshot)
        object.__setattr__(self, "disposition", PlanDisposition.NO_RECOVERY)
        object.__setattr__(self, "steps", ())


RecoveryPlan = ActionPlan | HaltPlan | NoRecoveryPlan

_AUTHORIZED_TOKEN = object()


@dataclass(frozen=True, slots=True, init=False)
class AuthorizedStep:
    plan: RecoveryPlan
    step_index: int
    step: TransformEffectTuple | RemoveScratch

    def __init__(
        self,
        *,
        plan: RecoveryPlan,
        step_index: int,
        step: TransformEffectTuple | RemoveScratch,
        _construction_token: object | None = None,
    ) -> None:
        if _construction_token is not _AUTHORIZED_TOKEN:
            raise TypeError("AuthorizedStep values are created only by authorize_recovery_step")
        object.__setattr__(self, "plan", plan)
        object.__setattr__(self, "step_index", step_index)
        object.__setattr__(self, "step", step)


_STEP_TYPES = {
    TransitionTransactionState,
    TransitionEffectState,
    TransformEffectTuple,
    RemoveScratch,
    PreserveExternal,
    DetachActive,
}


def _validate_steps(steps: tuple[RecoveryStep, ...]) -> None:
    if type(steps) is not tuple or any(type(step) not in _STEP_TYPES for step in steps):
        raise ProtocolError("plan steps must be an exact tuple of closed step variants")
    for step in steps:
        if type(step) is TransformEffectTuple and type(step.variant) is not EffectVariant:
            raise ProtocolError("transform variant must be an exact EffectVariant")
        if type(step) is not TransitionTransactionState:
            continue
        has_result = step.rollback_result is not None
        has_diagnostic = step.halt_diagnostic is not None
        if step.to_state is TransactionState.ROLLED_BACK:
            valid = has_result and not has_diagnostic
        elif step.to_state is TransactionState.HALTED:
            valid = has_diagnostic and not has_result
        else:
            valid = not has_result and not has_diagnostic
        if not valid:
            raise ProtocolError("transaction transition has invalid terminal payloads")


def _new_action_plan(
    *,
    bound_snapshot: RecoverySnapshot,
    disposition: PlanDisposition,
    steps: tuple[RecoveryStep, ...],
) -> ActionPlan:
    _validate_steps(steps)
    expected_result = {
        PlanDisposition.ROLL_BACK: RollbackResult.RESTORED,
        PlanDisposition.ROLL_BACK_REFUSED: RollbackResult.EXTERNAL_DRIFT_PRESERVED,
        PlanDisposition.COMMITTED_CLEANUP: None,
        PlanDisposition.DETACH_TERMINAL: None,
    }
    if disposition not in expected_result:
        raise ProtocolError("action plan has a non-action disposition")
    rollback_result = expected_result[disposition]
    return ActionPlan(
        bound_snapshot=bound_snapshot,
        disposition=disposition,
        steps=steps,
        rollback_result=rollback_result,
        _construction_token=_PLAN_TOKEN,
    )


def _new_halt_plan(
    *,
    bound_snapshot: RecoverySnapshot,
    diagnostic: HaltDiagnostic,
    steps: tuple[TransitionTransactionState, ...],
) -> HaltPlan:
    if bound_snapshot.transaction_state is TransactionState.HALTED:
        valid = not steps and bound_snapshot.halt_diagnostic == diagnostic
    else:
        valid = (
            len(steps) == 1
            and steps[0].from_state is bound_snapshot.transaction_state
            and steps[0].to_state is TransactionState.HALTED
            and steps[0].rollback_result is None
            and steps[0].halt_diagnostic == diagnostic
        )
    if not valid:
        raise ProtocolError("halt plan has an invalid transition or diagnostic")
    return HaltPlan(
        bound_snapshot=bound_snapshot,
        diagnostic=diagnostic,
        steps=steps,
        _construction_token=_PLAN_TOKEN,
    )


def _new_no_recovery_plan(bound_snapshot: RecoverySnapshot) -> NoRecoveryPlan:
    return NoRecoveryPlan(
        bound_snapshot=bound_snapshot,
        _construction_token=_PLAN_TOKEN,
    )


def _new_authorized_step(
    plan: RecoveryPlan,
    step_index: int,
    step: TransformEffectTuple | RemoveScratch,
) -> AuthorizedStep:
    if type(step) not in {TransformEffectTuple, RemoveScratch}:
        raise ProtocolError("authorized step must be filesystem-mutating")
    return AuthorizedStep(
        plan=plan,
        step_index=step_index,
        step=step,
        _construction_token=_AUTHORIZED_TOKEN,
    )
