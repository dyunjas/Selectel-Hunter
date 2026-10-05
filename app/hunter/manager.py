"""Backward-compatible imports for the account BURST scheduler."""

from .account_manager import AccountSchedulerManager, BurstSchedulerManager

TaskManager = AccountSchedulerManager
LegacyTaskManager = AccountSchedulerManager

__all__ = ["AccountSchedulerManager", "BurstSchedulerManager", "TaskManager", "LegacyTaskManager"]
