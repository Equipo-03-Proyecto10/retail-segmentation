"""deploy-service.sh deploys one service, and rolls back that one alone (F18-03).

Each case runs the real script and the real assemble-acl.sh in a scratch tree,
with stand-ins for docker, curl, nginx, systemctl and redis-cli on PATH. The
stand-ins log every call and keep the state the script reads back: which tag
each project runs and on which port, which units are active, and whether an
installed include would pass `nginx -t`. Readiness holds when a running
project publishes the registry's port, so a release published elsewhere is a
release whose /health/ready cannot pass, as in deploy/rehearse-rollback.sh.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    shutil.which("flock") is None or shutil.which("sha256sum") is None,
    reason="the deploy script needs util-linux flock and coreutils sha256sum",
)

PORTS = {"alpha": 5901, "beta": 5902}

STUBS = {
    "docker": r"""#!/usr/bin/env bash
echo "docker $*" >>"$FAKE_STATE/calls"
if [[ $1 == compose ]]; then
  shift
  project= envfile= file=
  while [[ $# -gt 0 ]]; do
    case $1 in
      -p) project=$2; shift 2 ;;
      --env-file) envfile=$2; shift 2 ;;
      -f) file=$2; shift 2 ;;
      *) break ;;
    esac
  done
  tag=$(sed -n 's/^IMAGE_TAG=//p' "$envfile")
  case $1 in
    pull) [[ " ${FAKE_PULL_FAILS:-} " != *" $tag "* ]] ;;
    config) echo "ghcr.io/equipo-03-proyecto10/$project:$tag" ;;
    up) port=$(grep -oE '127\.0\.0\.1:[0-9]+:' "$file" | cut -d: -f2)
        echo "$tag $port" >"$FAKE_STATE/running-$project" ;;
    down) rm -f "$FAKE_STATE/running-$project" ;;
  esac
  exit
fi
[[ $1 == image && $2 == inspect && -n ${FAKE_IMAGE_MISSING:-} ]] && exit 1
exit 0
""",
    "curl": r"""#!/usr/bin/env bash
echo "curl $*" >>"$FAKE_STATE/calls"
url=${*: -1}
port=${url#http://127.0.0.1:}
port=${port%%/*}
held=0
[[ -f $FAKE_STATE/hold-$port ]] && held=1
for f in "$FAKE_STATE"/running-*; do
  [[ -f $f ]] || continue
  read -r tag published <"$f"
  unready=" ${FAKE_UNREADY_TAGS:-} "
  if [[ $published == "$port" && $held == 0 && $unready != *" $tag "* ]]; then
    [[ $* == *http_code* ]] && printf ' (HTTP 204)'
    exit 0
  fi
done
[[ " $* " == *" -fsS "* ]] && exit 22
printf '{"code":"schema_not_ready"} (HTTP 503)'
""",
    "nginx": r"""#!/usr/bin/env bash
echo "nginx $*" >>"$FAKE_STATE/calls"
! grep -rqs BROKEN "$NGINX_API_DIR"
""",
    "systemctl": r"""#!/usr/bin/env bash
echo "systemctl $*" >>"$FAKE_STATE/calls"
args=()
for a in "$@"; do [[ $a == --quiet ]] || args+=("$a"); done
case ${args[0]} in
  cat) [[ -z ${FAKE_NO_UNIT:-} ]] ;;
  is-active) [[ -f $FAKE_STATE/active-${args[1]} ]] ;;
  start) touch "$FAKE_STATE/active-${args[1]}" ;;
  *) exit 0 ;;
esac
""",
    "fake-redis-cli": r"""#!/usr/bin/env bash
echo "redis-cli $*" >>"$FAKE_STATE/calls"
echo OK
""",
}


class Instance:
    """A scratch instance: the merged commit's tree and the host's paths."""

    def __init__(self, base: Path):
        self.base = base
        self.tree = base / "tree"
        self.services = base / "opt/services"
        self.svc = base / "etc/svc"
        self.nginx = base / "etc/nginx/mosaiq-api.d"
        self.state = base / "state"
        self.acl_file = base / "etc/redis/users.acl"
        for directory in (self.services, self.svc, self.state, base / "bin"):
            directory.mkdir(parents=True)
        for name, text in STUBS.items():
            path = base / "bin" / name
            path.write_text(text)
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        scripts = ("deploy/deploy-service.sh", "deploy/infra/redis/assemble-acl.sh")
        for relative in scripts:
            (self.tree / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, self.tree / relative)
        (self.tree / "contracts").mkdir()
        (self.tree / "contracts/registry.yaml").write_text(
            "services:\n"
            + "".join(f"  - name: {n}\n    port: {p}\n" for n, p in PORTS.items())
        )
        (base / "ops.env").write_text("REDIS_OPS_PASSWORD=ops-secret\n")
        for name in PORTS:
            (self.svc / f"{name}.env").write_text(
                f"REDIS_{name.upper()}_PASSWORD=pw-{name}\nALPHA_KID='kid-1'\n"
            )

    def release(
        self,
        name: str,
        version: str,
        *,
        port: int | None = None,
        acl: str = "",
        include: str = "",
    ) -> None:
        """Put services/<name>/ of a merged commit in the tree."""
        src = self.tree / "services" / name
        shutil.rmtree(src, ignore_errors=True)
        (src / "nginx").mkdir(parents=True)
        (src / "redis").mkdir()
        (src / "VERSION").write_text(version.split("+")[0] + "\n")
        published = port or PORTS[name]
        kid = "      - /keys/auth-${ALPHA_KID}.pem:/run/key.pem:ro\n"
        (src / "compose.yaml").write_text(
            f"name: mosaiq-{name}\nservices:\n  {name}:\n"
            f"    image: ghcr.io/equipo-03-proyecto10/mosaiq-{name}:${{IMAGE_TAG}}\n"
            f'    ports:\n      - "127.0.0.1:{published}:{PORTS[name]}"\n'
            + (f"    volumes:\n{kid}" if name == "alpha" else "")
        )
        (src / "nginx" / f"{name}.conf").write_text(
            f"location /api/{name}/ {{}}  # {version} {include}\n"
        )
        (src / "redis" / f"{name}.acl").write_text(
            f"user svc_{name} on {{{{password}}}} ~{name}:cache:*{acl}\n"
        )

    def deploy(
        self, name: str, version: str, background: bool = False, **env: str
    ) -> subprocess.CompletedProcess[str] | subprocess.Popen[str]:
        environment = {
            "PATH": f"{self.base / 'bin'}:/usr/bin:/bin",
            "HOME": str(self.base),
            "REQUIRE_ROOT": "0",
            "SERVICES_ROOT": str(self.services),
            "SVC_ENV_DIR": str(self.svc),
            "NGINX_API_DIR": str(self.nginx),
            "REDIS_OPS_ENV": str(self.base / "ops.env"),
            "DEPLOY_LOCK": str(self.base / "deploy.lock"),
            "ACL_FILE": str(self.acl_file),
            "REDIS_STATE_CLI": "fake-redis-cli state",
            "REDIS_CACHE_CLI": "fake-redis-cli cache",
            "READY_SECONDS": "1",
            "READY_INTERVAL": "0.1",
            "FAKE_STATE": str(self.state),
        } | env
        command = ["bash", str(self.tree / "deploy/deploy-service.sh"), name, version]
        if background:
            return subprocess.Popen(
                command,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        return subprocess.run(command, env=environment, text=True, capture_output=True)

    def ship(self, name: str, version: str, **release: object) -> None:
        self.release(name, version, **release)
        result = self.deploy(name, version)
        assert result.returncode == 0, result.stdout + result.stderr

    def current(self, name: str) -> str | None:
        link = self.services / name / "current"
        return os.readlink(link) if link.is_symlink() else None

    def running(self, name: str) -> str | None:
        path = self.state / f"running-mosaiq-{name}"
        return path.read_text().strip() if path.exists() else None

    def include(self, name: str) -> str | None:
        path = self.nginx / f"{name}.conf"
        return path.read_text() if path.exists() else None

    def acl(self) -> list[str]:
        return self.acl_file.read_text().splitlines()

    def calls(self) -> list[str]:
        path = self.state / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def forget_calls(self) -> None:
        (self.state / "calls").unlink(missing_ok=True)


def user_line(name: str, rules: str) -> str:
    digest = hashlib.sha256(f"pw-{name}".encode()).hexdigest()
    return f"user svc_{name} on #{digest} {rules}"


@pytest.fixture
def instance(tmp_path: Path) -> Instance:
    return Instance(tmp_path)


def test_a_first_release_runs_the_seven_steps_in_order(instance: Instance) -> None:
    instance.release("alpha", "0.1.0")
    result = instance.deploy("alpha", "0.1.0")

    assert result.returncode == 0, result.stdout + result.stderr
    assert instance.current("alpha") == "releases/0.1.0"
    staged = instance.services / "alpha/releases/0.1.0"
    assert sorted(p.name for p in staged.iterdir()) == [
        "compose.yaml",
        "nginx",
        "redis",
        "release.env",
    ]
    # The key id the compose file interpolates comes from the service's env file.
    assert (staged / "release.env").read_text() == "IMAGE_TAG=0.1.0\nALPHA_KID=kid-1\n"
    assert stat.S_IMODE((staged / "release.env").stat().st_mode) == 0o600
    assert instance.running("alpha") == "0.1.0 5901"
    assert instance.include("alpha") == "location /api/alpha/ {}  # 0.1.0 \n"
    assert user_line("alpha", "~alpha:cache:*") in instance.acl()
    assert "user default off ~* &* +@all" in instance.acl()

    calls = instance.calls()
    order = [
        "redis-cli state ACL LOAD",
        "redis-cli cache ACL LOAD",
        "docker compose -p mosaiq-alpha",
        "curl -fsS",
        "nginx -t",
        "systemctl reload nginx",
        "systemctl enable --quiet mosaiq-svc@alpha",
        "systemctl start mosaiq-svc@alpha",
    ]
    positions = [
        next(i for i, call in enumerate(calls) if call.startswith(p)) for p in order
    ]
    assert positions == sorted(positions), calls
    compose = [call for call in calls if call.startswith("docker compose")]
    assert [call.split()[8] for call in compose] == [
        "pull",
        "config",
        "up",
    ]
    assert all(" -p mosaiq-alpha " in call for call in compose)
    assert "up -d --no-deps" in compose[-1]


def test_an_upgrade_switches_current_and_keeps_the_previous_release(
    instance: Instance,
) -> None:
    instance.ship("alpha", "0.1.0")
    instance.release("alpha", "0.2.0", acl=" ~alpha:v2:*")
    result = instance.deploy("alpha", "0.2.0")

    assert result.returncode == 0, result.stdout + result.stderr
    assert instance.current("alpha") == "releases/0.2.0"
    assert (instance.services / "alpha/releases/0.1.0/release.env").exists()
    assert instance.running("alpha") == "0.2.0 5901"
    assert "# 0.2.0" in instance.include("alpha")
    assert user_line("alpha", "~alpha:cache:* ~alpha:v2:*") in instance.acl()
    assert "previous: releases/0.1.0" in result.stdout


def test_an_unready_release_rolls_back_this_service_alone(instance: Instance) -> None:
    instance.ship("alpha", "0.1.0")
    instance.ship("beta", "0.1.0")
    acl_before = instance.acl()
    include_before = instance.include("alpha")
    instance.forget_calls()

    instance.release("alpha", "0.2.0", port=15901, acl=" ~alpha:v2:*")
    result = instance.deploy("alpha", "0.2.0")

    assert result.returncode == 70, result.stdout + result.stderr
    assert "FAILED at step 5" in result.stdout
    assert "schema_not_ready" in result.stderr
    assert "releases/0.1.0 (image tag 0.1.0) is serving again" in result.stdout
    assert instance.current("alpha") == "releases/0.1.0"
    assert instance.running("alpha") == "0.1.0 5901"
    assert instance.include("alpha") == include_before
    assert instance.acl() == acl_before
    # The include step was never reached, and nothing of beta was touched.
    assert not any(call.startswith("nginx") for call in instance.calls())
    assert not any("beta" in call for call in instance.calls())
    assert instance.running("beta") == "0.1.0 5902"


def test_a_refused_include_puts_the_previous_one_back(instance: Instance) -> None:
    instance.ship("alpha", "0.1.0")
    acl_before = instance.acl()
    include_before = instance.include("alpha")

    instance.release("alpha", "0.2.0", acl=" ~alpha:v2:*", include="BROKEN")
    result = instance.deploy("alpha", "0.2.0")

    assert result.returncode == 70, result.stdout + result.stderr
    assert "FAILED at step 6" in result.stdout
    assert instance.include("alpha") == include_before
    assert instance.current("alpha") == "releases/0.1.0"
    assert instance.running("alpha") == "0.1.0 5901"
    assert instance.acl() == acl_before
    assert instance.calls()[-1].startswith("curl")


def test_a_failed_pull_leaves_the_running_container_alone(instance: Instance) -> None:
    instance.ship("alpha", "0.1.0")
    acl_before = instance.acl()
    instance.forget_calls()

    instance.release("alpha", "0.2.0", acl=" ~alpha:v2:*")
    result = instance.deploy("alpha", "0.2.0", FAKE_PULL_FAILS="0.2.0")

    assert result.returncode == 70, result.stdout + result.stderr
    assert "FAILED at step 4" in result.stdout
    assert instance.running("alpha") == "0.1.0 5901"
    assert instance.acl() == acl_before
    assert not any(" up " in call for call in instance.calls())


def test_an_image_missing_after_the_pull_is_never_started(instance: Instance) -> None:
    instance.release("alpha", "0.1.0")
    result = instance.deploy("alpha", "0.1.0", FAKE_IMAGE_MISSING="1")

    assert result.returncode == 70, result.stdout + result.stderr
    assert "is not on disk after the pull" in result.stderr
    assert not any(" up " in call for call in instance.calls())


def test_a_failed_first_release_leaves_nothing_behind(instance: Instance) -> None:
    instance.ship("beta", "0.1.0")
    instance.release("alpha", "0.1.0", port=15901)
    result = instance.deploy("alpha", "0.1.0")

    assert result.returncode == 70, result.stdout + result.stderr
    assert "had no release here before, and none is running now" in result.stdout
    assert instance.current("alpha") is None
    assert instance.running("alpha") is None
    assert instance.include("alpha") is None
    assert not any("svc_alpha" in line for line in instance.acl())
    assert any("svc_beta" in line for line in instance.acl())
    assert any(call.endswith(" down") for call in instance.calls())


def test_a_missing_env_file_is_refused_before_anything_changes(
    instance: Instance,
) -> None:
    (instance.svc / "alpha.env").unlink()
    instance.release("alpha", "0.1.0")
    result = instance.deploy("alpha", "0.1.0")

    assert result.returncode == 2
    assert "refusing to start" in result.stderr
    assert "nothing was changed" in result.stderr
    assert instance.calls() == []
    assert not (instance.services / "alpha").exists()


def test_a_version_other_than_the_version_file_is_refused(instance: Instance) -> None:
    instance.release("alpha", "0.1.0")
    result = instance.deploy("alpha", "0.2.0")

    assert result.returncode == 2
    assert "services/alpha/VERSION is 0.1.0, not 0.2.0" in result.stderr
    assert instance.calls() == []


@pytest.mark.parametrize("version", ["1.0", "v1.0.0", "01.0.0", "1.0.0-rc1", "1.0.0+"])
def test_a_version_that_is_not_semver_is_refused(
    instance: Instance, version: str
) -> None:
    result = instance.deploy("alpha", version)
    assert result.returncode == 64
    assert instance.calls() == []


def test_a_variable_the_env_file_lacks_is_refused_while_staging(
    instance: Instance,
) -> None:
    (instance.svc / "alpha.env").write_text("REDIS_ALPHA_PASSWORD=pw-alpha\n")
    instance.release("alpha", "0.1.0")
    result = instance.deploy("alpha", "0.1.0")

    assert result.returncode == 2
    assert "compose.yaml needs ALPHA_KID" in result.stderr
    assert not any(
        call.startswith(("docker", "redis-cli")) for call in instance.calls()
    )
    assert list((instance.services / "alpha/releases").iterdir()) == []


def test_the_release_already_serving_is_only_checked(instance: Instance) -> None:
    instance.ship("alpha", "0.1.0")
    instance.forget_calls()
    result = instance.deploy("alpha", "0.1.0")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Already serving 0.1.0" in result.stdout
    assert all(call.startswith(("systemctl cat", "curl")) for call in instance.calls())


def test_a_rollback_that_cannot_get_ready_needs_a_person(instance: Instance) -> None:
    instance.ship("alpha", "0.1.0")
    instance.release("alpha", "0.2.0", port=15901)
    result = instance.deploy("alpha", "0.2.0", FAKE_UNREADY_TAGS="0.1.0")

    assert result.returncode == 71, result.stdout + result.stderr
    assert "ROLLBACK ALSO FAILED" in result.stderr


def test_build_metadata_stages_a_separate_release_of_the_same_tag(
    instance: Instance,
) -> None:
    instance.ship("alpha", "0.1.0")
    instance.release("alpha", "0.1.0+rehearsal", port=15901)
    result = instance.deploy("alpha", "0.1.0+rehearsal")

    assert result.returncode == 70, result.stdout + result.stderr
    rehearsal = instance.services / "alpha/releases/0.1.0+rehearsal"
    assert (rehearsal / "release.env").read_text().startswith("IMAGE_TAG=0.1.0\n")
    assert instance.current("alpha") == "releases/0.1.0"
    assert instance.running("alpha") == "0.1.0 5901"


def wait_for_call(instance: Instance, prefix: str) -> None:
    deadline = time.monotonic() + 20
    while not any(call.startswith(prefix) for call in instance.calls()):
        assert time.monotonic() < deadline, f"no call starting {prefix!r}"
        time.sleep(0.05)


def test_a_failed_deploy_does_not_hold_back_a_concurrent_one(
    instance: Instance,
) -> None:
    instance.ship("alpha", "0.1.0")
    instance.ship("beta", "0.1.0")
    instance.forget_calls()

    instance.release("alpha", "0.2.0", port=15901, acl=" ~alpha:v2:*")
    instance.release("beta", "0.2.0", acl=" ~beta:v2:*")
    failing = instance.deploy("alpha", "0.2.0", background=True, READY_SECONDS="3")
    wait_for_call(instance, "docker compose -p mosaiq-alpha --env-file")
    passing = instance.deploy("beta", "0.2.0")
    output, _ = failing.communicate(timeout=30)

    assert failing.returncode == 70, output
    assert passing.returncode == 0, passing.stdout + passing.stderr
    assert instance.current("alpha") == "releases/0.1.0"
    assert instance.current("beta") == "releases/0.2.0"
    assert user_line("alpha", "~alpha:cache:*") in instance.acl()
    assert user_line("beta", "~beta:cache:* ~beta:v2:*") in instance.acl()


def test_a_concurrent_deploy_keeps_the_line_another_one_just_loaded(
    instance: Instance,
) -> None:
    # Beta's step 3 runs while alpha 0.2.0 waits for readiness. Built from each
    # service's current release, the aclfile would give alpha its 0.1.0 line
    # back and keep it once alpha switches to 0.2.0.
    instance.ship("alpha", "0.1.0")
    instance.ship("beta", "0.1.0")
    instance.forget_calls()

    hold = instance.state / "hold-5901"
    hold.touch()
    instance.release("alpha", "0.2.0", acl=" ~alpha:v2:*")
    instance.release("beta", "0.2.0", acl=" ~beta:v2:*")
    slow = instance.deploy("alpha", "0.2.0", background=True, READY_SECONDS="20")
    wait_for_call(instance, "docker compose -p mosaiq-alpha --env-file")
    passing = instance.deploy("beta", "0.2.0")
    hold.unlink()
    output, _ = slow.communicate(timeout=30)

    assert passing.returncode == 0, passing.stdout + passing.stderr
    assert slow.returncode == 0, output
    assert user_line("alpha", "~alpha:cache:* ~alpha:v2:*") in instance.acl()
    assert user_line("beta", "~beta:cache:* ~beta:v2:*") in instance.acl()


def test_the_committed_scripts_are_executable() -> None:
    for relative in ("deploy/deploy-service.sh", "deploy/rehearse-rollback.sh"):
        mode = subprocess.run(
            ["git", "ls-files", "--stage", relative],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()[0]
        assert mode == "100755", relative
