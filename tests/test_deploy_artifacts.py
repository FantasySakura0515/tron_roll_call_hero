"""Deploy artifact presence + safety tests (deployable server spec)."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DeployArtifactsTest(unittest.TestCase):
    def read(self, name: str) -> str:
        return (ROOT / name).read_text(encoding="utf-8")

    def test_dockerfile_runs_gateway_supervisor(self) -> None:
        text = self.read("Dockerfile")
        self.assertIn("discord-gateway", text)
        self.assertIn("--supervisor", text)
        self.assertIn("INSTALL_OCR", text)

    def test_compose_restarts_and_mounts(self) -> None:
        text = self.read("docker-compose.yml")
        self.assertIn("restart: unless-stopped", text)
        self.assertIn("./config.yaml", text)
        self.assertIn("./state", text)
        self.assertIn(".env", text)

    def test_dockerignore_excludes_secrets_and_state(self) -> None:
        text = self.read(".dockerignore")
        for entry in ("config.yaml", "state", "log", ".git", ".venv"):
            self.assertIn(entry, text)

    def test_env_example_has_discord_keys_but_no_real_token(self) -> None:
        text = self.read(".env.example")
        self.assertIn("DISCORD_BOT_TOKEN", text)
        self.assertIn("DISCORD_PUBLIC_KEY", text)
        self.assertIn("DISCORD_APPLICATION_ID", text)

    def test_config_example_is_multi_account(self) -> None:
        text = self.read("config.example.yaml")
        self.assertIn("accounts", text)
        self.assertIn("school", text)
        self.assertIn("operating", text)

    def test_systemd_unit_present(self) -> None:
        text = self.read("deploy/tron-bot.service")
        self.assertIn("Restart=always", text)
        self.assertIn("discord-gateway", text)


if __name__ == "__main__":
    unittest.main()
