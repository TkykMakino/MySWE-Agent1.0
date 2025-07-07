"""
Run on a batch of FEA-Bench instances.

This script is a modified version of run_batch.py, specifically tailored to handle
the data format of FEA-Bench (https://github.com/microsoft/FEA-Bench).

[cyan][bold]=== EXAMPLE ===[/bold][/cyan]

[green]
sweagent run-fea-bench \\
    --instances_path /path/to/your/FEA-Bench-v1.0-medium.jsonl \\
    --config config/default.yaml \\
    --agent.model.name gpt-4o \\
    --suffix fea_bench_run
[/green]
"""

import getpass
import json
import logging
import random
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from pathlib import Path
from typing import Self

import yaml
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from rich.live import Live
from swerex.deployment.hooks.status import SetStatusDeploymentHook

from sweagent import TRAJECTORY_DIR
from sweagent.agent.agents import AgentConfig, get_agent_from_config
from sweagent.agent.hooks.status import SetStatusAgentHook
from sweagent.environment.hooks.status import SetStatusEnvironmentHook
from sweagent.environment.swe_env import SWEEnv
from sweagent.exceptions import ModelConfigurationError, TotalCostLimitExceededError
from sweagent.run._progress import RunBatchProgressManager
from sweagent.run.batch_instances import (
    BatchInstance,
    BatchInstances,
    ProblemStatement,
)
from sweagent.run.common import BasicCLI, ConfigHelper, save_predictions
from sweagent.run.hooks.abstract import CombinedRunHooks, RunHook
from sweagent.run.hooks.apply_patch import SaveApplyPatchHook
from sweagent.run.merge_predictions import merge_predictions
from sweagent.run.run_single import RunSingleConfig
from sweagent.types import AgentRunResult
from sweagent.utils.config import load_environment_variables
from sweagent.utils.log import (
    add_file_handler,
    add_logger_names_to_stream_handlers,
    get_logger,
    register_thread_name,
    remove_file_handler,
    set_stream_handler_levels,
)

logger = get_logger("swea-run")


# ★★★ここからがFEA-Bench専用の改造部分★★★
class FEABenchInstances(BatchInstances):
    """
    Loads instances from an FEA-Bench .jsonl file.
    This class handles the conversion from the FEA-Bench data format
    to the format expected by SWE-Agent.
    """

    path: Path = Field(
        validation_alias="instances_path", description="Path to the FEA-Bench .jsonl file."
    )

    def get_instance_configs(self) -> list[BatchInstance]:
        """Reads the jsonl file and converts each line to a BatchInstance."""
        logger.info(f"Loading FEA-Bench instances from {self.path}")
        if not self.path.exists():
            raise FileNotFoundError(f"File not found: {self.path}")

        lines = self.path.read_text().splitlines()
        fea_bench_dicts = [json.loads(line) for line in lines if line]

        converted_instances = []
        for i, data in enumerate(fea_bench_dicts):
            # FEA-BenchのデータをSWE-Agentの形式に変換
            try:
                problem_info = data.get("problem_info", {})
                problem_statement_text = (
                    f"Title: {problem_info.get('pr_title', '')}\n\n"
                    f"Body:\n{problem_info.get('pr_body', '')}"
                )

                # 全ての追加フィールドをextra_fieldsに格納
                extra_fields = {
                    "patch": data.get("patch"),
                    "test_patch": data.get("test_patch"),
                    "pull_number": data.get("pull_number"),
                    "url": data.get("url"),
                    "issue_numbers": data.get("issue_numbers", []),
                    "first_commit_time": data.get("first_commit_time"),
                    "created_at": data.get("created_at"),
                    "readmes": data.get("readmes"),
                    "files": data.get("files"),
                    "non_py_patch": data.get("non_py_patch"),
                    "new_components": data.get("new_components"),
                    "FAIL_TO_PASS": data.get("FAIL_TO_PASS", []),
                    "PASS_TO_PASS": data.get("PASS_TO_PASS", []),
                    "environment_setup_commit": data.get("environment_setup_commit"),
                }

                # Assume `testbed` directory is in the same parent directory as the data file
                testbed_path = self.path.parent / "testbed"
                repo_folder_name = data['repo'].replace('/', '__')
                repo_local_path = testbed_path / repo_folder_name

                logger.info(
                    f"Converting instance '{data.get('instance_id')}': "
                    f"repo '{data['repo']}' -> path '{repo_local_path.resolve()}'"
                )

                from sweagent.run.batch_instances import SimpleBatchInstance
                from swerex.deployment.config import DockerDeploymentConfig

                simple_dict = {
                    "instance_id": data["instance_id"],
                    "repo_name": str(repo_local_path.resolve()),
                    "base_commit": data["base_commit"],
                    "problem_statement": problem_statement_text,
                    "image_name": "sweagent/swe-agent:latest",
                    "extra_fields": extra_fields
                }

                simple_instance = SimpleBatchInstance.model_validate(simple_dict)
                deployment = DockerDeploymentConfig(image="sweagent/swe-agent:latest")
                converted_instances.append(simple_instance.to_full_batch_instance(deployment))

            except KeyError as e:
                logger.error(f"Missing key {e} in FEA-Bench data at line {i+1}. Skipping instance.")
                continue

        logger.info(f"Successfully converted {len(converted_instances)} instances.")
        return converted_instances

# ★★★ここまでがFEA-Bench専用の改造部分★★★


class RunBatchConfig(BaseSettings, cli_implicit_flags=False):
    instances: FEABenchInstances = Field(description="Instances to run from FEA-Bench.")
    agent: AgentConfig = Field(description="Agent options.")
    output_dir: Path = Field(default=Path("DEFAULT"), description="Output directory.")
    suffix: str = ""
    raise_exceptions: bool = False
    redo_existing: bool = False
    env_var_path: Path | None = None
    num_workers: int = Field(default=1)
    random_delay_multiplier: float = 0.3
    progress_bar: bool = True

    model_config = SettingsConfigDict(extra="forbid", env_prefix="SWE_AGENT_")

    def set_default_output_dir(self) -> None:
        if self.output_dir == Path("DEFAULT"):
            user_id = getpass.getuser()
            source_id = Path(self.instances.path.name).stem
            try:
                model_id = self.agent.model.id
            except AttributeError:
                model_id = "unknown"
            config_file = getattr(self, "_config_files", ["no_config"])[0]
            if config_file != "no_config":
                config_file = Path(config_file).stem
            suffix = f"__{self.suffix}" if self.suffix else ""
            self.output_dir = TRAJECTORY_DIR / user_id / f"{config_file}__{model_id}___{source_id}{suffix}"


class _BreakLoop(Exception):
    """Used for internal control flow"""


class RunBatch:
    def __init__(
        self,
        instances: list[BatchInstance],
        agent_config: AgentConfig,
        *,
        output_dir: Path = Path("."),
        hooks: list[RunHook] | None = None,
        raise_exceptions: bool = False,
        redo_existing: bool = False,
        num_workers: int = 1,
        progress_bar: bool = True,
        random_delay_multiplier: float = 0.3,
    ):
        if self._model_id in ["human", "human_thought"] and num_workers > 1:
            msg = "Cannot run with human model in parallel"
            raise ValueError(msg)

        self.logger = get_logger("swea-run", emoji="🏃")
        add_file_handler(
            output_dir / "run_batch.log",
            id_="progress",
            filter=lambda name: "swea-run" in name or "config" in name,
        )
        self.instances = instances
        self.agent_config = agent_config
        self.output_dir = output_dir
        self._raise_exceptions = raise_exceptions
        self._chooks = CombinedRunHooks()
        self._redo_existing = redo_existing
        self._num_workers = min(num_workers, len(instances))
        for hook in hooks or [SaveApplyPatchHook(show_success_message=False)]:
            self.add_hook(hook)
        self._progress_manager = RunBatchProgressManager(
            num_instances=len(instances), yaml_report_path=output_dir / "run_batch_exit_statuses.yaml"
        )
        self._show_progress_bar = progress_bar
        self._random_delay_multiplier = random_delay_multiplier

    @property
    def _model_id(self) -> str:
        try:
            return self.agent_config.model.id
        except AttributeError:
            return "unknown"

    @classmethod
    def from_config(cls, config: RunBatchConfig) -> Self:
        load_environment_variables(config.env_var_path)
        config.set_default_output_dir()
        config.output_dir.mkdir(parents=True, exist_ok=True)
        (config.output_dir / "run_batch.config.yaml").write_text(yaml.dump(config.model_dump_json(), indent=2))
        logger.debug("Loading instances from %s", f"{config.instances!r}")
        instances = config.instances.get_instance_configs()
        logger.info("Loaded %d instances", len(instances))
        if not instances:
            msg = "No instances to run. Please check the path to your .jsonl file."
            raise ValueError(msg)
        logger.debug("The first instance is %s", f"{instances[0]!r}")
        rb = cls(
            instances=instances,
            agent_config=config.agent,
            output_dir=config.output_dir,
            raise_exceptions=config.raise_exceptions,
            redo_existing=config.redo_existing,
            num_workers=config.num_workers,
            progress_bar=config.progress_bar,
            random_delay_multiplier=config.random_delay_multiplier,
        )
        return rb

    def add_hook(self, hook: RunHook) -> None:
        hook.on_init(run=self)
        self._chooks.add_hook(hook)

    def main(self) -> None:
        self.logger.info("Starting run. Find output files at %s", self.output_dir)
        self._chooks.on_start()

        if self._num_workers <= 1:
            self.main_single_worker()
        else:
            self.main_multi_worker()

        output_dirs = [self.output_dir / instance.problem_statement.id for instance in self.instances]
        merge_predictions(output_dirs, self.output_dir / "preds.json")

        self._chooks.on_end()

    def main_single_worker(self) -> None:
        with ExitStack() as stack:
            if self._model_id not in ["human", "human_thought"] and self._show_progress_bar:
                stack.enter_context(Live(self._progress_manager.render_group))
            for instance in self.instances:
                try:
                    self.run_instance(instance)
                except _BreakLoop:
                    self.logger.info("Stopping loop over instances")
                    break

    def main_multi_worker(self) -> None:
        add_logger_names_to_stream_handlers()
        set_stream_handler_levels(logging.WARNING)
        self.logger.setLevel(logging.INFO)

        with Live(self._progress_manager.render_group):
            with ThreadPoolExecutor(max_workers=self._num_workers) as executor:
                futures = [executor.submit(self.run_instance, instance) for instance in self.instances]
                try:
                    for future in as_completed(futures):
                        future.result()
                except (KeyboardInterrupt, _BreakLoop):
                    msg = "Received keyboard interrupt, waiting for running instances to finish, but cancelled everything else"
                    self.logger.info(msg)
                    executor.shutdown(wait=False, cancel_futures=True)
                finally:
                    self._progress_manager.print_report()

    def run_instance(self, instance: BatchInstance) -> None:
        self.logger.info("Running on instance %s", instance.problem_statement.id)
        register_thread_name(instance.problem_statement.id)
        self._add_instance_log_file_handlers(instance.problem_statement.id, multi_worker=self._num_workers > 1)
        if self._progress_manager.n_completed < self._num_workers:
            time.sleep(random.random() * self._random_delay_multiplier * (self._num_workers - 1))

        self._progress_manager.on_instance_start(instance.problem_statement.id)

        if previous_exit_status := self.should_skip(instance):
            self._progress_manager.on_instance_end(
                instance.problem_statement.id, exit_status=f"skipped ({previous_exit_status})"
            )
            self._remove_instance_log_file_handlers(instance.problem_statement.id)
            return

        try:
            result = self._run_instance(instance)
        except KeyboardInterrupt:
            raise _BreakLoop
        except (SystemExit, ModelConfigurationError, TotalCostLimitExceededError) as e:
            if self._raise_exceptions:
                raise
            self.logger.critical(f"❌ Exiting because {e.__class__.__name__} was called")
            raise _BreakLoop
        except Exception as e:
            self.logger.error(traceback.format_exc())
            self.logger.error(f"❌ Failed on {instance.problem_statement.id}: {e}")
            self._progress_manager.on_uncaught_exception(instance.problem_statement.id, e)
            if self._raise_exceptions:
                raise
        else:
            self._progress_manager.on_instance_end(
                instance.problem_statement.id, exit_status=result.info.get("exit_status", "unknown_exit")
            )
        finally:
            self._progress_manager.update_exit_status_table()
            self._remove_instance_log_file_handlers(instance.problem_statement.id)

    def _run_instance(self, instance: BatchInstance) -> AgentRunResult:
        output_dir = self.output_dir / instance.problem_statement.id
        output_dir.mkdir(parents=True, exist_ok=True)
        self.agent_config.name = f"{instance.problem_statement.id}"
        agent = get_agent_from_config(self.agent_config)
        
        single_run_replay_config = RunSingleConfig(
            agent=self.agent_config,
            problem_statement=instance.problem_statement,
            env=instance.env,
        )
        (output_dir / f"{instance.problem_statement.id}.config.yaml").write_text(
            yaml.dump(single_run_replay_config.model_dump_json(), indent=2)
        )
        agent.replay_config = single_run_replay_config
        agent.add_hook(SetStatusAgentHook(instance.problem_statement.id, self._progress_manager.update_instance_status))
        self._progress_manager.update_instance_status(instance.problem_statement.id, "Starting environment")
        
        instance.env.name = f"{instance.problem_statement.id}"
        env = SWEEnv.from_config(instance.env)
        env.add_hook(
            SetStatusEnvironmentHook(instance.problem_statement.id, self._progress_manager.update_instance_status)
        )
        env.deployment.add_hook(
            SetStatusDeploymentHook(instance.problem_statement.id, self._progress_manager.update_instance_status)
        )
        
        try:
            env.start()
            self._chooks.on_instance_start(index=0, env=env, problem_statement=instance.problem_statement)
            result = agent.run(
                problem_statement=instance.problem_statement,
                env=env,
                output_dir=output_dir,
            )
        except Exception:
            agent.logger.error(traceback.format_exc())
            raise
        finally:
            env.close()
        
        save_predictions(self.output_dir, instance.problem_statement.id, result)
        self._chooks.on_instance_completed(result=result)
        return result

    def should_skip(self, instance: BatchInstance) -> bool | str:
        if self._redo_existing:
            return False
        log_path = self.output_dir / instance.problem_statement.id / (instance.problem_statement.id + ".traj")
        if not log_path.exists():
            return False
        content = log_path.read_text()
        if not content.strip():
            self.logger.warning("Found empty trajectory: %s. Removing.", log_path)
            log_path.unlink()
            return False
        try:
            data = json.loads(content)
            exit_status = data["info"].get("exit_status", None)
            if exit_status == "early_exit" or exit_status is None:
                self.logger.warning(f"Found existing trajectory with no exit status: {log_path}. Removing.")
                log_path.unlink()
                return False
        except Exception as e:
            self.logger.error(f"Failed to check existing trajectory: {log_path}: {e}. Removing.")
            log_path.unlink()
            return False
        self.logger.info(f"⏭️ Skipping existing trajectory: {log_path}")
        return exit_status

    def _add_instance_log_file_handlers(self, instance_id: str, multi_worker: bool = False) -> None:
        filename_template = f"{instance_id}.{{level}}.log"
        for level in ["trace", "debug", "info"]:
            filter_str = instance_id if multi_worker else ""
            add_file_handler(
                self.output_dir / instance_id / filename_template.format(level=level),
                filter=filter_str,
                level=level,
                id_=f"{instance_id}-{level}",
            )

    def _remove_instance_log_file_handlers(self, instance_id: str) -> None:
        for level in ["trace", "debug", "info"]:
            remove_file_handler(f"{instance_id}-{level}")


def run_from_config(config: RunBatchConfig):
    RunBatch.from_config(config).main()


def run_from_cli(args: list[str] | None = None):
    if args is None:
        args = sys.argv[1:]
    assert __doc__ is not None
    help_text = (
        __doc__ + "\n[cyan][bold]=== ALL THE OPTIONS ===[/bold][/cyan]\n\n" + ConfigHelper().get_help(RunBatchConfig)
    )
    run_from_config(BasicCLI(RunBatchConfig, help_text=help_text).get_config(args))


if __name__ == "__main__":
    run_from_cli()
