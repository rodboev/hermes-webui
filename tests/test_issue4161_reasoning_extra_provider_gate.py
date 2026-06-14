"""Regression tests for #4161: provider-gate title-gen reasoning extra_body.

Covers:
  - _route_rejects_reasoning_extra() returns True for OpenAI and Azure routes
  - _route_rejects_reasoning_extra() returns False for local/openrouter/other routes
  - generate_title_raw_via_aux() omits reasoning from extra_body for OpenAI routes
  - generate_title_raw_via_aux() includes reasoning in extra_body for local routes
  - MiniMax routes still get reasoning_split alongside reasoning
"""
import sys
import types
import unittest
from unittest.mock import patch

# Stub agent.auxiliary_client so it is importable without hermes-agent installed.
_agent_stub = types.ModuleType('agent')
_aux_stub = types.ModuleType('agent.auxiliary_client')
sys.modules.setdefault('agent', _agent_stub)
sys.modules.setdefault('agent.auxiliary_client', _aux_stub)
_agent_stub.auxiliary_client = _aux_stub


def _patch_tg_config(config_dict):
    return patch('agent.auxiliary_client._get_auxiliary_task_config', return_value=config_dict, create=True)


def _make_resp(content='Test Title'):
    return types.SimpleNamespace(
        choices=[
            types.SimpleNamespace(
                message=types.SimpleNamespace(content=content),
                finish_reason='stop',
            )
        ]
    )


class TestRouteRejectsReasoningExtra(unittest.TestCase):
    """Unit tests for _route_rejects_reasoning_extra()."""

    def _call(self, provider='', model='', base_url=''):
        from api.streaming import _route_rejects_reasoning_extra
        return _route_rejects_reasoning_extra(provider=provider, model=model, base_url=base_url)

    # --- True cases ---

    def test_openai_api_provider_returns_true(self):
        self.assertTrue(self._call(provider='openai-api'))

    def test_openai_provider_returns_true(self):
        self.assertTrue(self._call(provider='openai'))

    def test_openai_codex_provider_returns_true(self):
        self.assertTrue(self._call(provider='openai-codex'))

    def test_azure_provider_returns_true(self):
        self.assertTrue(self._call(provider='azure'))

    def test_azure_slash_deployment_returns_true(self):
        self.assertTrue(self._call(provider='azure/deployment'))

    def test_openai_com_base_url_returns_true(self):
        self.assertTrue(self._call(base_url='https://api.openai.com/v1/'))

    def test_openai_azure_com_base_url_returns_true(self):
        self.assertTrue(self._call(base_url='https://myorg.openai.azure.com/openai/deployments/gpt-4o'))

    def test_openai_provider_case_insensitive(self):
        self.assertTrue(self._call(provider='OpenAI-API'))

    def test_azure_provider_case_insensitive(self):
        self.assertTrue(self._call(provider='AZURE'))

    # --- False cases ---

    def test_openrouter_provider_returns_false(self):
        """openrouter proxies to reasoning-capable backends; must not be blocked."""
        self.assertFalse(self._call(provider='openrouter'))

    def test_empty_provider_returns_false(self):
        """Auto/local routes need the reasoning disable."""
        self.assertFalse(self._call(provider=''))

    def test_anthropic_provider_returns_false(self):
        self.assertFalse(self._call(provider='anthropic'))

    def test_custom_provider_returns_false(self):
        self.assertFalse(self._call(provider='custom'))

    def test_lmstudio_provider_returns_false(self):
        self.assertFalse(self._call(provider='lmstudio'))

    def test_minimax_provider_returns_false(self):
        self.assertFalse(self._call(provider='minimax'))

    def test_localhost_base_url_returns_false(self):
        self.assertFalse(self._call(base_url='http://localhost:11434'))

    def test_empty_base_url_returns_false(self):
        self.assertFalse(self._call(base_url=''))

    def test_all_empty_returns_false(self):
        self.assertFalse(self._call(provider='', model='', base_url=''))

    def test_ollama_provider_returns_false(self):
        self.assertFalse(self._call(provider='ollama'))

    def test_openrouter_in_model_does_not_trigger_true(self):
        """Model field containing 'openai' must not cause a false positive."""
        self.assertFalse(self._call(provider='openrouter', model='openai/gpt-4o'))


class TestGenerateTitleRawViaAuxReasoningGate(unittest.TestCase):
    """Integration tests: verify extra_body contents per provider route."""

    def _run(self, tg_config, provider='', model='', base_url=''):
        from api.streaming import generate_title_raw_via_aux

        captured = {}

        def fake_call_llm(**kwargs):
            captured.update(kwargs)
            return _make_resp('Test Title')

        with _patch_tg_config(tg_config):
            with patch('agent.auxiliary_client.call_llm', side_effect=fake_call_llm, create=True):
                result, status = generate_title_raw_via_aux(
                    user_text='What is the weather?',
                    assistant_text='It is sunny.',
                    provider=provider,
                    model=model,
                    base_url=base_url,
                )

        return result, status, captured

    def test_openai_route_omits_reasoning_from_extra_body(self):
        """Regression for #4161: OpenAI rejects extra_body reasoning parameter."""
        _, status, captured = self._run(
            tg_config={'provider': 'openai-api', 'model': 'gpt-4o-mini', 'base_url': ''},
        )
        extra_body = captured.get('extra_body')
        self.assertEqual(status, 'llm_aux')
        # extra_body must be None or not contain 'reasoning'
        if extra_body is not None:
            self.assertNotIn('reasoning', extra_body)

    def test_openai_base_url_omits_reasoning_from_extra_body(self):
        """Provider resolved via api.openai.com base_url must also skip reasoning."""
        _, status, captured = self._run(
            tg_config={'provider': '', 'model': 'gpt-4o', 'base_url': 'https://api.openai.com/v1/'},
        )
        extra_body = captured.get('extra_body')
        self.assertEqual(status, 'llm_aux')
        if extra_body is not None:
            self.assertNotIn('reasoning', extra_body)

    def test_local_route_includes_reasoning_in_extra_body(self):
        """Local/auto routes must still send reasoning disable to suppress reasoning tokens."""
        _, status, captured = self._run(
            tg_config={'provider': '', 'model': 'qwen3-14b', 'base_url': 'http://localhost:11434'},
        )
        extra_body = captured.get('extra_body')
        self.assertEqual(status, 'llm_aux')
        self.assertIsNotNone(extra_body)
        self.assertIn('reasoning', extra_body)
        self.assertEqual(extra_body['reasoning'], {'enabled': False})

    def test_openrouter_route_includes_reasoning_in_extra_body(self):
        """openrouter proxies to reasoning models; it must receive the disable flag."""
        _, status, captured = self._run(
            tg_config={
                'provider': 'openrouter',
                'model': 'anthropic/claude-haiku-title',
                'base_url': 'https://openrouter.ai/api/v1',
            },
        )
        extra_body = captured.get('extra_body')
        self.assertEqual(status, 'llm_aux')
        self.assertIsNotNone(extra_body)
        self.assertIn('reasoning', extra_body)
        self.assertEqual(extra_body['reasoning'], {'enabled': False})

    def test_minimax_route_includes_reasoning_and_reasoning_split(self):
        """MiniMax routes need both reasoning disable and reasoning_split."""
        _, status, captured = self._run(
            tg_config={
                'provider': 'minimax',
                'model': 'minimax-text-01',
                'base_url': 'https://api.minimaxi.com/v1',
            },
        )
        extra_body = captured.get('extra_body')
        self.assertEqual(status, 'llm_aux')
        self.assertIsNotNone(extra_body)
        self.assertIn('reasoning', extra_body)
        self.assertEqual(extra_body['reasoning'], {'enabled': False})
        self.assertIn('reasoning_split', extra_body)
        self.assertTrue(extra_body['reasoning_split'])

    def test_azure_route_omits_reasoning_from_extra_body(self):
        """Azure OpenAI rejects the reasoning parameter just like OpenAI does."""
        _, status, captured = self._run(
            tg_config={'provider': 'azure', 'model': 'gpt-4o', 'base_url': ''},
        )
        extra_body = captured.get('extra_body')
        self.assertEqual(status, 'llm_aux')
        if extra_body is not None:
            self.assertNotIn('reasoning', extra_body)

    def test_caller_supplied_openai_route_omits_reasoning(self):
        """Caller-supplied openai-api route must also be gated."""
        _, status, captured = self._run(
            tg_config={},
            provider='openai-api',
            model='gpt-4o-mini',
            base_url='',
        )
        extra_body = captured.get('extra_body')
        self.assertEqual(status, 'llm_aux')
        if extra_body is not None:
            self.assertNotIn('reasoning', extra_body)


if __name__ == '__main__':
    unittest.main()
