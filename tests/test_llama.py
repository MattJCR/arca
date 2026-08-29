from __future__ import annotations

import os
import socket
import sqlite3
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from arca.config import library_db_path
from arca.db import open_library_rw
from arca.llama import (
    GROUP_COMPUTE,
    GROUP_CONTEXT,
    GROUP_EMBEDDINGS,
    GROUP_GPU,
    GROUP_MEMORY,
    GROUP_MODEL,
    GROUP_OTHER,
    GROUP_ROPE,
    GROUP_SAMPLING,
    GROUP_SERVER,
    HEALTH_DOWN,
    HEALTH_LOADING,
    HEALTH_OK,
    MODE_FORM,
    MODE_RAW,
    NOTE_CONTEXT_OVER_TRAINED,
    NOTE_GPU_LAYERS_OVER_BLOCKS,
    NOTE_GPU_LAYERS_WITHOUT_GPU,
    NOTE_MEMORY_OVER_AVAILABLE,
    NOTE_MODEL_MISSING,
    SOURCE_CONFIGURED,
    SOURCE_VENDOR,
    STAGE_GENERATION,
    STAGE_HEALTH,
    STAGE_OK,
    BinaryInfo,
    HelpOption,
    LlamaServer,
    PreflightNote,
    Profile,
    apply_profile,
    base_url_for,
    default_profile,
    delete_profile,
    device_memory_bytes,
    effective_command,
    estimate_memory,
    find_binary,
    format_command,
    grouped_help,
    has_gpu,
    inspect_binary,
    last_good_profile,
    list_profiles,
    mark_last_good,
    parse_devices,
    parse_help,
    preflight,
    profile_argv,
    read_help,
    read_model,
    read_profile,
    run_probe,
    save_profile,
    set_default_profile,
    version_line,
)
from tests.fixtures.llama_server_stub import HELP_TEXT
from tests.support import LLAMA_STUB, free_port, stub_args, stub_command, write_gguf


@pytest.fixture
def server() -> Iterator[LlamaServer]:
    instance = LlamaServer()
    yield instance
    instance.stop()


def test_a_missing_binary_is_reported_without_raising(tmp_path: Path) -> None:
    path, source = find_binary(tmp_path, configured="")
    if path is not None:
        pytest.skip("this machine has llama-server on the path")
    assert source == ""
    info = inspect_binary(tmp_path)
    assert not info.present
    assert info.version == ""


def test_a_vendored_binary_wins_over_the_path(tmp_path: Path) -> None:
    vendored = tmp_path / "vendor" / "llama.cpp" / "build" / "bin" / "llama-server"
    vendored.parent.mkdir(parents=True)
    vendored.write_text("", "utf-8")
    path, source = find_binary(tmp_path)
    assert path == vendored
    assert source == SOURCE_VENDOR


def test_a_configured_binary_wins_over_discovery(tmp_path: Path) -> None:
    vendored = tmp_path / "vendor" / "llama.cpp" / "llama-server"
    vendored.parent.mkdir(parents=True)
    vendored.write_text("", "utf-8")
    chosen = tmp_path / "elsewhere" / "llama-server"
    chosen.parent.mkdir(parents=True)
    chosen.write_text("", "utf-8")
    path, source = find_binary(tmp_path, configured=str(chosen))
    assert path == chosen
    assert source == SOURCE_CONFIGURED


def test_a_configured_binary_that_does_not_exist_is_not_invented(tmp_path: Path) -> None:
    path, source = find_binary(tmp_path, configured=str(tmp_path / "nope" / "llama-server"))
    assert path is None
    assert source == ""


def test_the_version_and_devices_come_from_the_binary_itself() -> None:
    output, error = run_probe([sys.executable, str(LLAMA_STUB), "--version"])
    assert error == ""
    assert version_line(output) == "version: 9999 (abcdef1)"
    devices_output, _ = run_probe([sys.executable, str(LLAMA_STUB), "--list-devices"])
    devices = parse_devices(devices_output)
    assert devices == ("CPU: stub processor (0 MiB, 0 MiB free)",)
    assert has_gpu(devices, devices_output) is False


def test_a_gpu_build_is_detected_from_its_device_list() -> None:
    output, _ = run_probe([sys.executable, str(LLAMA_STUB), "--list-devices", "--stub-gpu"])
    devices = parse_devices(output)
    assert has_gpu(devices, output) is True


def test_probing_a_binary_that_cannot_run_reports_the_error(tmp_path: Path) -> None:
    output, error = run_probe([str(tmp_path / "not-a-binary")])
    assert output == ""
    assert error


def test_the_effective_command_line_is_visible(server: LlamaServer) -> None:
    port = free_port()
    server.start(stub_command(port))
    status = server.status()
    assert status.running
    assert status.pid
    assert str(port) in status.command_line
    assert status.command_line.startswith(format_command([sys.executable]))


def test_the_server_becomes_healthy_and_the_log_is_live(server: LlamaServer) -> None:
    port = free_port()
    server.start(stub_command(port))
    assert server.wait_healthy(timeout=30.0)
    assert server.health() == HEALTH_OK
    assert server.probe().health == HEALTH_OK
    assert any("listening" in line for line in server.log())


def test_stopping_the_server_leaves_it_down_but_keeps_the_log(server: LlamaServer) -> None:
    port = free_port()
    server.start(stub_command(port))
    assert server.wait_healthy(timeout=30.0)
    status = server.stop()
    assert not status.running
    assert server.health(timeout=1.0) == HEALTH_DOWN
    assert any("listening" in line for line in server.log())


def test_restarting_uses_the_same_command(server: LlamaServer) -> None:
    port = free_port()
    server.start(stub_command(port))
    assert server.wait_healthy(timeout=30.0)
    first = server.status().pid
    status = server.restart()
    assert status.running
    assert status.pid != first
    assert server.wait_healthy(timeout=30.0)


def test_a_loading_server_reports_loading_not_down(server: LlamaServer) -> None:
    port = free_port()
    server.start(stub_command(port, "--stub-delay", "30"))
    for _ in range(60):
        if server.health(timeout=1.0) == HEALTH_LOADING:
            break
    assert server.health(timeout=1.0) == HEALTH_LOADING
    assert server.status().running


def test_a_binary_that_refuses_to_start_is_reported_with_its_log(server: LlamaServer) -> None:
    port = free_port()
    server.start(stub_command(port, "--stub-fail"))
    assert server.wait_healthy(timeout=10.0) is False
    assert not server.status().running
    assert server.status().exit_code == 1
    assert any("refusing to start" in line for line in server.log())


def test_an_unrunnable_command_degrades_instead_of_raising(
    server: LlamaServer, tmp_path: Path
) -> None:
    dead = LlamaServer(default_url="http://127.0.0.1:1")
    status = dead.start([str(tmp_path / "missing-binary"), "--port", "1"])
    assert not status.running
    assert status.error
    assert dead.health(timeout=1.0) == HEALTH_DOWN


def test_an_empty_command_is_refused(server: LlamaServer) -> None:
    status = server.start([])
    assert not status.running
    assert status.error


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ([], "http://127.0.0.1:9999"),
        (["llama-server"], "http://127.0.0.1:8080"),
        (["llama-server", "--port", "9090"], "http://127.0.0.1:9090"),
        (["llama-server", "--port=9090"], "http://127.0.0.1:9090"),
        (["llama-server", "--host", "0.0.0.0", "--port", "1"], "http://127.0.0.1:1"),
        (["llama-server", "--host", "192.168.1.5"], "http://192.168.1.5:8080"),
    ],
)
def test_the_base_url_comes_from_the_effective_command(command: list[str], expected: str) -> None:
    assert base_url_for(command, "http://127.0.0.1:9999") == expected


def test_command_formatting_quotes_paths_with_spaces() -> None:
    assert format_command(["llama-server", "--model", "C:/a b/m.gguf"]) == (
        'llama-server --model "C:/a b/m.gguf"'
    )


def _options(text: str = HELP_TEXT) -> dict[str, HelpOption]:
    return {option.flag: option for option in parse_help(text)}


def test_the_form_is_built_from_the_help_of_the_binary() -> None:
    options = _options()
    context = options["--ctx-size"]
    assert context.value == "N"
    assert context.aliases == ("-c",)
    assert "size of the prompt context" in context.description
    assert context.default == "4096"
    assert context.env == "LLAMA_ARG_CTX_SIZE"
    assert context.group == GROUP_CONTEXT
    assert context.section == "common params"


def test_aliases_collapse_onto_the_curated_flag() -> None:
    layers = _options()["--n-gpu-layers"]
    assert set(layers.aliases) == {"-ngl", "--gpu-layers"}
    assert layers.group == GROUP_GPU
    assert layers.value == "N"


def test_a_switch_without_a_value_is_reported_as_boolean() -> None:
    flash = _options()["--flash-attn"]
    assert flash.boolean
    assert flash.group == GROUP_COMPUTE
    assert "Flash Attention" in flash.description


def test_an_unknown_flag_lands_in_the_other_group() -> None:
    unknown = _options()["--flavour-of-the-month"]
    assert unknown.group == GROUP_OTHER
    assert unknown.value == "STRING"


def test_sampling_flags_are_kept_apart_from_the_launch_form() -> None:
    options = _options()
    assert options["--temp"].group == GROUP_SAMPLING
    assert options["--top-k"].group == GROUP_SAMPLING


def test_a_renamed_flag_in_a_new_binary_regenerates_the_form_without_code_changes() -> None:
    renamed = HELP_TEXT.replace("--ctx-size N", "--context-window N")
    options = _options(renamed)
    assert "--ctx-size" not in options
    assert options["--context-window"].group == GROUP_OTHER
    assert options["--context-window"].value == "N"


def test_the_help_form_is_grouped_in_the_curated_order() -> None:
    groups = [name for name, _ in grouped_help(parse_help(HELP_TEXT))]
    assert groups == [
        GROUP_MODEL,
        GROUP_CONTEXT,
        GROUP_COMPUTE,
        GROUP_GPU,
        GROUP_MEMORY,
        GROUP_ROPE,
        GROUP_SERVER,
        GROUP_EMBEDDINGS,
        GROUP_SAMPLING,
        GROUP_OTHER,
    ]


def test_the_help_text_comes_from_running_the_binary() -> None:
    text, error = read_help([sys.executable, str(LLAMA_STUB)])
    assert error == ""
    assert _options(text)["--ctx-size"].value == "N"


@pytest.fixture
def library(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_library_rw(library_db_path(tmp_path))
    yield connection
    connection.close()


def test_profiles_round_trip_through_the_database(library: sqlite3.Connection) -> None:
    saved = save_profile(
        library, Profile(name="gpu", args={"--ctx-size": "4096", "--flash-attn": ""})
    )
    assert saved.mode == MODE_FORM
    assert saved.created_at
    assert read_profile(library, "gpu") == saved
    assert [profile.name for profile in list_profiles(library)] == ["gpu"]
    assert delete_profile(library, "gpu")
    assert read_profile(library, "gpu") is None


def test_the_form_profile_becomes_a_command_line(library: sqlite3.Connection) -> None:
    profile = save_profile(
        library,
        Profile(name="cpu", args={"--model": "m.gguf", "--flash-attn": "", "--ctx-size": "2048"}),
    )
    assert profile_argv(profile) == [
        "--model",
        "m.gguf",
        "--flash-attn",
        "--ctx-size",
        "2048",
    ]
    assert effective_command(Path("llama-server"), profile)[0] == "llama-server"


def test_raw_mode_replaces_the_form_instead_of_mixing_with_it(library: sqlite3.Connection) -> None:
    profile = save_profile(
        library,
        Profile(
            name="raw",
            mode=MODE_RAW,
            args={"--ctx-size": "2048"},
            raw_cmd='--model "C:/models/a b.gguf" --ctx-size 8192',
        ),
    )
    assert profile_argv(profile) == ["--model", "C:/models/a b.gguf", "--ctx-size", "8192"]


def test_only_one_profile_is_default_and_only_one_is_last_good(
    library: sqlite3.Connection,
) -> None:
    save_profile(library, Profile(name="one"))
    save_profile(library, Profile(name="two"))
    set_default_profile(library, "one")
    mark_last_good(library, "one")
    set_default_profile(library, "two")
    mark_last_good(library, "two")
    assert _name(default_profile(library)) == "two"
    assert _name(last_good_profile(library)) == "two"
    assert [profile.is_default for profile in list_profiles(library)] == [False, True]


def test_saving_a_profile_again_keeps_its_standing(library: sqlite3.Connection) -> None:
    save_profile(library, Profile(name="one"))
    mark_last_good(library, "one")
    set_default_profile(library, "one")
    updated = save_profile(library, Profile(name="one", args={"--ctx-size": "512"}))
    assert updated.last_good
    assert updated.is_default
    assert updated.args == {"--ctx-size": "512"}


def test_a_context_beyond_the_trained_one_warns_without_blocking(tmp_path: Path) -> None:
    model = write_gguf(tmp_path / "chat.gguf", context=4096)
    notes = _codes(preflight(["--model", str(model), "--ctx-size", "8192"], _cpu_binary()))
    assert NOTE_CONTEXT_OVER_TRAINED in notes


def test_more_gpu_layers_than_the_model_has_is_pointed_out(tmp_path: Path) -> None:
    model = write_gguf(tmp_path / "chat.gguf", blocks=32)
    notes = _codes(preflight(["--model", str(model), "--n-gpu-layers", "99"], _gpu_binary()))
    assert NOTE_GPU_LAYERS_OVER_BLOCKS in notes


def test_gpu_layers_on_a_binary_without_gpu_support_are_flagged(tmp_path: Path) -> None:
    model = write_gguf(tmp_path / "chat.gguf")
    notes = _codes(preflight(["--model", str(model), "-ngl", "40"], _cpu_binary()))
    assert NOTE_GPU_LAYERS_WITHOUT_GPU in notes


def test_an_estimate_over_the_available_memory_gives_the_number(tmp_path: Path) -> None:
    model = write_gguf(tmp_path / "chat.gguf", context=4096, embedding=4096, blocks=32)
    found = [
        note
        for note in preflight(["--model", str(model), "-c", "4000000"], _cpu_binary())
        if note.code == NOTE_MEMORY_OVER_AVAILABLE
    ]
    assert found
    assert float(found[0].params["needed_gib"]) > float(found[0].params["available_gib"])
    assert found[0].params["target"] == "ram"


def test_a_missing_model_file_is_reported_before_starting(tmp_path: Path) -> None:
    notes = _codes(preflight(["--model", str(tmp_path / "absent.gguf")], _cpu_binary()))
    assert notes == [NOTE_MODEL_MISSING]


def test_a_sound_command_raises_no_warnings(tmp_path: Path) -> None:
    model = write_gguf(tmp_path / "chat.gguf", context=8192, blocks=32)
    assert preflight(["--model", str(model), "--ctx-size", "2048"], _cpu_binary()) == []


def test_the_kv_cache_type_changes_the_estimate(tmp_path: Path) -> None:
    model = read_model(write_gguf(tmp_path / "chat.gguf"), "chat")
    half = estimate_memory(model, 4096, "q8_0", "q8_0")
    full = estimate_memory(model, 4096, "f16", "f16")
    assert half < full


def test_vram_is_read_from_the_device_list() -> None:
    assert device_memory_bytes(("CUDA0: card (24576 MiB, 24000 MiB free)",)) == 24576 * 1024 * 1024
    assert device_memory_bytes(("CPU: processor (0 MiB, 0 MiB free)",)) == 0


def _codes(notes: list[PreflightNote]) -> list[str]:
    return [note.code for note in notes]


def _name(profile: Profile | None) -> str:
    return profile.name if profile is not None else ""


def _cpu_binary() -> BinaryInfo:
    return BinaryInfo(path=Path("llama-server"), source="path", version="stub", gpu=False)


def _gpu_binary() -> BinaryInfo:
    return BinaryInfo(
        path=Path("llama-server"),
        source="path",
        version="stub",
        devices=("CUDA0: card (24576 MiB, 24000 MiB free)",),
        gpu=True,
    )


def _saved(connection: sqlite3.Connection, name: str, *extra: str) -> Profile:
    port = free_port()
    return save_profile(
        connection,
        Profile(name=name, mode=MODE_RAW, raw_cmd=format_command(stub_args(port, *extra))),
    )


def test_a_profile_that_works_is_kept_as_the_last_good_one(
    library: sqlite3.Connection, server: LlamaServer
) -> None:
    profile = _saved(library, "good")
    result = apply_profile(
        library, server, Path(sys.executable), profile, health_timeout=30.0, generation_timeout=30.0
    )
    assert result.ok
    assert result.trial.stage == STAGE_OK
    assert "stub reply" in result.trial.reply
    assert _name(last_good_profile(library)) == "good"
    assert _name(default_profile(library)) == "good"
    assert server.status().running


def test_a_broken_profile_reverts_to_the_last_good_one_and_shows_its_log(
    library: sqlite3.Connection, server: LlamaServer
) -> None:
    good = _saved(library, "good")
    apply_profile(
        library, server, Path(sys.executable), good, health_timeout=30.0, generation_timeout=30.0
    )
    broken = _saved(library, "broken", "--stub-fail")
    result = apply_profile(
        library,
        server,
        Path(sys.executable),
        broken,
        health_timeout=10.0,
        generation_timeout=10.0,
    )
    assert not result.ok
    assert result.trial.stage == STAGE_HEALTH
    assert any("refusing to start" in line for line in result.trial.log)
    assert result.rolled_back_to == "good"
    assert result.rollback_ok
    assert _name(default_profile(library)) == "good"
    assert _name(last_good_profile(library)) == "good"
    assert server.health() == HEALTH_OK


def test_a_server_that_answers_health_but_never_generates_is_rejected(
    library: sqlite3.Connection, server: LlamaServer
) -> None:
    good = _saved(library, "good")
    apply_profile(
        library, server, Path(sys.executable), good, health_timeout=30.0, generation_timeout=30.0
    )
    mute = _saved(library, "mute", "--stub-mute")
    result = apply_profile(
        library, server, Path(sys.executable), mute, health_timeout=30.0, generation_timeout=10.0
    )
    assert not result.ok
    assert result.trial.stage == STAGE_GENERATION
    assert result.rolled_back_to == "good"
    assert server.health() == HEALTH_OK


def test_a_first_profile_that_fails_leaves_nothing_running(
    library: sqlite3.Connection, server: LlamaServer
) -> None:
    broken = _saved(library, "broken", "--stub-fail")
    result = apply_profile(
        library, server, Path(sys.executable), broken, health_timeout=10.0, generation_timeout=10.0
    )
    assert not result.ok
    assert result.active == ""
    assert result.rolled_back_to == ""
    assert not server.status().running


@pytest.mark.skipif(os.name != "nt", reason="the job object is a Windows mechanism")
def test_a_hard_kill_of_the_parent_takes_the_engine_with_it() -> None:
    code = (
        "import sys, time\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
        "from arca.llama import LlamaServer\n"
        "from tests.support import stub_command\n"
        "port = int(sys.argv[1])\n"
        "server = LlamaServer(default_url=f'http://127.0.0.1:{port}')\n"
        "print(server.start(stub_command(port)).pid, flush=True)\n"
        "time.sleep(120)\n"
    )
    port = free_port()
    parent = subprocess.Popen(
        [sys.executable, "-c", code, str(port)], stdout=subprocess.PIPE, text=True
    )
    try:
        assert parent.stdout is not None
        parent.stdout.readline()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not _answers(port):
            time.sleep(0.5)
        assert _answers(port), "the child never came up"
    finally:
        parent.kill()
        parent.wait(timeout=30)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and _answers(port):
        time.sleep(0.5)
    assert not _answers(port), "the engine outlived the process that started it"


def _answers(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(1.0)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def test_a_profile_that_would_take_the_embedding_port_is_warned_about(tmp_path: Path) -> None:
    model = write_gguf(tmp_path / "chat.gguf")
    argv = ["llama-server", "--model", str(model), "--port", "8080"]
    codes = {note.code for note in preflight(argv, BinaryInfo(None), "http://127.0.0.1:8080")}
    assert "port_taken" in codes
    elsewhere = {note.code for note in preflight(argv, BinaryInfo(None), "http://127.0.0.1:8099")}
    assert "port_taken" not in elsewhere
    assert not {note.code for note in preflight(argv, BinaryInfo(None))} & {"port_taken"}


def test_a_profile_without_a_port_lands_on_the_default_and_is_warned(tmp_path: Path) -> None:
    model = write_gguf(tmp_path / "chat.gguf")
    argv = ["llama-server", "--model", str(model)]
    codes = {note.code for note in preflight(argv, BinaryInfo(None), "http://127.0.0.1:8080")}
    assert "port_taken" in codes
