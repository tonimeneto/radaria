from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from radaria import (
    Publication,
    anthropic_publications,
    build_consumption_output,
    deepmind_publications,
    build_collected_publication,
    extract_editorial_content,
    extract_anthropic_structured_content,
    google_developer_publication,
    google_publications,
    github_publications,
    latest_editorial_publication,
    next_publication,
    next_from_publications,
    persist_discard,
    persist_result,
    publication_identity,
    register_active_candidate,
    remember_publication_urls,
    rss_feed_entries,
    result_path,
)


FIXTURES = Path(__file__).parent / "fixtures"


class LatestEditorialPublicationTests(unittest.TestCase):
    def test_selects_latest_openai_editorial_page(self) -> None:
        rss = (FIXTURES / "openai-news.xml").read_text(encoding="utf-8")

        publication = latest_editorial_publication(rss)

        self.assertEqual(publication.title, "Publicação editorial esperada")
        self.assertEqual(
            publication.url, "https://openai.com/index/expected-publication"
        )
        self.assertEqual(
            publication.published_at,
            datetime(2026, 9, 3, 11, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(publication.categories, ("Research", "Product"))


class ExtractEditorialContentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = (FIXTURES / "openai-article.html").read_text(encoding="utf-8")
        self.content = extract_editorial_content(
            self.html, "Publicação editorial esperada"
        )

    def test_extracts_only_expected_editorial_blocks(self) -> None:
        self.assertEqual(
            self.content,
            [
                "Primeiro parágrafo com uma referência.",
                "Seção principal",
                "Segundo parágrafo editorial.",
            ],
        )

    def test_discards_navigation_footer_and_interface_elements(self) -> None:
        extracted_text = "\n".join(self.content)
        discarded_texts = (
            "Navegação global",
            "Sumário da publicação",
            "opens in a new window",
            "Etiquetas, autor e notas da interface",
            "Keep reading",
            "Recomendação de outra publicação",
            "Rodapé dentro do artigo",
            "Rodapé global",
        )

        for discarded_text in discarded_texts:
            with self.subTest(discarded_text=discarded_text):
                self.assertNotIn(discarded_text, extracted_text)

    def test_extracts_anthropic_article_without_related_content(self) -> None:
        html = (FIXTURES / "anthropic-article.html").read_text(encoding="utf-8")

        content = extract_editorial_content(
            html,
            "Nova publicação da Anthropic",
            ("keep reading", "related content"),
        )

        self.assertEqual(
            content,
            [
                "Primeiro parágrafo editorial.",
                "Detalhes técnicos",
                "Segundo parágrafo editorial.",
            ],
        )

    def test_extracts_anthropic_structured_feature_content(self) -> None:
        html = r'''<script>self.__next_f.push([1,"{\"chapters\":[{\"_type\":\"block\",\"children\":[{\"_type\":\"span\",\"text\":\"Bloco editorial.\"}]},{\"_type\":\"historySlackBlock\",\"message\":\"Mensagem técnica da história.\"}]}"])</script>'''

        content = extract_anthropic_structured_content(html, "Título")

        self.assertEqual(
            content, ["Bloco editorial.", "Mensagem técnica da história."]
        )


class AnthropicPublicationTests(unittest.TestCase):
    def test_discovers_official_publications_with_shared_internal_format(self) -> None:
        html = (FIXTURES / "anthropic-news.html").read_text(encoding="utf-8")

        publications = anthropic_publications(html)
        newest = max(publications, key=lambda publication: publication.published_at)

        self.assertEqual(len(publications), 2)
        self.assertEqual(newest.title, "Nova publicação da Anthropic")
        self.assertEqual(
            newest.url, "https://www.anthropic.com/news/new-publication"
        )
        self.assertEqual(newest.categories, ("Research",))
        self.assertEqual(newest.source, "Anthropic News")
        self.assertEqual(
            publication_identity(newest),
            "url:https://anthropic.com/news/new-publication",
        )


class GooglePublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.deepmind_rss = (FIXTURES / "google-deepmind.xml").read_text(
            encoding="utf-8"
        )
        self.developers_rss = (FIXTURES / "google-developers.xml").read_text(
            encoding="utf-8"
        )

    def test_reads_deepmind_rss_and_google_developer_metadata(self) -> None:
        deepmind = deepmind_publications(self.deepmind_rss)[0]
        developer_entry = rss_feed_entries(
            self.developers_rss, {"developers.googleblog.com"}
        )[0]
        developer_html = (FIXTURES / "google-developer-article.html").read_text(
            encoding="utf-8"
        )
        developer = google_developer_publication(developer_entry, developer_html)

        self.assertEqual(deepmind.source, "Google DeepMind")
        self.assertEqual(deepmind.title, "Publicação DeepMind atual")
        self.assertEqual(
            publication_identity(deepmind),
            "guid:https://deepmind.google/blog/current",
        )
        self.assertEqual(developer.source, "Google Developers Blog")
        self.assertEqual(
            developer.published_at,
            datetime(2026, 9, 4, tzinfo=timezone.utc),
        )

    def test_initializes_google_baselines_without_creating_history(self) -> None:
        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"

            publications = google_publications(
                self.deepmind_rss, self.developers_rss, state_path
            )
            selected = next_from_publications(publications, state_path)
            state = json.loads(state_path.read_text(encoding="utf-8"))

            self.assertIsNone(selected)
            self.assertEqual(
                set(state["source_baselines"]),
                {"google_deepmind", "google_developers_blog"},
            )

    def test_extracts_only_google_developer_editorial_container(self) -> None:
        html = (FIXTURES / "google-developer-article.html").read_text(
            encoding="utf-8"
        )

        content = extract_editorial_content(
            html,
            "Publicação técnica atual",
            root_tag="div",
            root_class="rich-content",
        )

        self.assertEqual(
            content,
            [
                "Primeiro bloco técnico.",
                "Padrão de engenharia",
                "Segundo bloco técnico.",
            ],
        )

    def test_extracts_deepmind_body_without_interface(self) -> None:
        html = (FIXTURES / "google-deepmind-article.html").read_text(
            encoding="utf-8"
        )

        content = extract_editorial_content(
            html,
            "Publicação DeepMind atual",
            ("keep reading", "get the latest news from google in your inbox"),
            root_tag="div",
            root_class="article-container__content",
        )

        self.assertEqual(
            content,
            [
                "Primeiro bloco DeepMind.",
                "Detalhes do modelo",
                "Segundo bloco DeepMind.",
            ],
        )


class GitHubPublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.blog_rss = (FIXTURES / "github-copilot-blog.xml").read_text(
            encoding="utf-8"
        )
        self.changelog_rss = (
            FIXTURES / "github-copilot-changelog.xml"
        ).read_text(encoding="utf-8")

    def test_normalizes_both_official_feeds_and_preserves_guid_query(self) -> None:
        with TemporaryDirectory() as directory:
            publications = github_publications(
                self.blog_rss,
                self.changelog_rss,
                Path(directory) / "state.json",
            )

        self.assertEqual(
            {publication.source for publication in publications},
            {"GitHub Copilot Blog", "GitHub Copilot Changelog"},
        )
        self.assertEqual(
            publication_identity(publications[0]),
            "guid:https://github.blog/?p=12345",
        )
        self.assertEqual(publications[0].categories, ("AI & ML", "GitHub Copilot"))

    def test_initializes_independent_github_baselines_without_history(self) -> None:
        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"

            publications = github_publications(
                self.blog_rss, self.changelog_rss, state_path
            )
            selected = next_from_publications(publications, state_path)
            state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertIsNone(selected)
        self.assertEqual(len(state["source_baselines"]["github_copilot_blog"]), 1)
        self.assertEqual(
            len(state["source_baselines"]["github_copilot_changelog"]), 1
        )

    def test_extracts_editorial_content_from_both_github_page_types(self) -> None:
        cases = (
            (
                "github-copilot-blog-article.html",
                "Orquestração de agentes no Copilot",
                "section",
                "post__content",
                [
                    "Primeiro bloco editorial do GitHub Blog.",
                    "Arquitetura",
                    "Segundo bloco editorial do GitHub Blog.",
                ],
            ),
            (
                "github-copilot-changelog-article.html",
                "Nova opção para coding agents",
                "div",
                "PostContent-main",
                [
                    "Primeiro bloco editorial do Changelog.",
                    "Disponibilidade",
                    "Segundo bloco editorial do Changelog.",
                ],
            ),
        )
        for fixture, title, root_tag, root_class, expected in cases:
            with self.subTest(fixture=fixture):
                html = (FIXTURES / fixture).read_text(encoding="utf-8")
                content = extract_editorial_content(
                    html, title, root_tag=root_tag, root_class=root_class
                )
                self.assertEqual(content, expected)
                self.assertNotIn("Navegação GitHub", "\n".join(content))
                self.assertNotIn("Rodapé GitHub", "\n".join(content))

class CollectedPublicationTests(unittest.TestCase):
    def test_builds_structured_input_with_only_relevant_editorial_content(self) -> None:
        publication = Publication(
            title="Título controlado",
            url="https://openai.com/index/controlled",
            published_at=datetime(2026, 9, 3, 11, 0, tzinfo=timezone.utc),
            categories=("Research",),
        )
        result = build_collected_publication(
            publication,
            [
                "September 3, 2026",
                "Author: OpenAI",
                "Conteúdo editorial necessário.",
            ],
        )

        self.assertEqual(result["title"], "Título controlado")
        self.assertEqual(result["source"], "OpenAI News")
        self.assertEqual(result["categories"], ["Research"])
        self.assertEqual(result["url"], publication.url)
        self.assertEqual(result["content"], "Conteúdo editorial necessário.")


class PublicationStateTests(unittest.TestCase):
    @staticmethod
    def structured_result(publication: Publication) -> dict:
        return {
            "id": publication_identity(publication),
            "title": publication.title,
            "source": "OpenAI News",
            "published_at": publication.published_at.isoformat(),
            "categories": list(publication.categories),
            "objective_summary": "Resumo objetivo.",
            "main_novelty": "Principal novidade.",
            "relevance_reason": "Motivo da relevância.",
            "original_url": publication.url,
        }

    def test_uses_normalized_guid_then_canonical_url_as_fallback(self) -> None:
        with_guid = Publication(
            title="Com GUID",
            url="https://openai.com/index/from-url",
            published_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
            categories=(),
            guid="https://WWW.OPENAI.COM/index/from-guid/?tracking=1#section",
        )
        without_guid = Publication(
            title="Sem GUID",
            url="https://WWW.OPENAI.COM/index/from-url/?tracking=1#section",
            published_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
            categories=(),
        )

        self.assertEqual(
            publication_identity(with_guid),
            "guid:https://openai.com/index/from-guid",
        )
        self.assertEqual(
            publication_identity(without_guid),
            "url:https://openai.com/index/from-url",
        )

    def test_first_discovery_then_no_new_publication_after_confirmation(self) -> None:
        rss = (FIXTURES / "openai-news.xml").read_text(encoding="utf-8")

        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            first = next_publication(rss, state_path)

            self.assertIsNotNone(first)
            assert first is not None
            self.assertEqual(first.title, "Publicação editorial esperada")

            identity = publication_identity(first)
            persisted_path = persist_result(
                self.structured_result(first),
                state_path,
                datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc),
            )
            second = next_publication(rss, state_path)

            self.assertEqual(persisted_path, result_path(identity, state_path))
            self.assertTrue(persisted_path.exists())
            self.assertIsNone(second)

    def test_does_not_mark_as_processed_when_persistence_fails(self) -> None:
        rss = (FIXTURES / "openai-news.xml").read_text(encoding="utf-8")

        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            publication = next_publication(rss, state_path)
            self.assertIsNotNone(publication)
            assert publication is not None
            (Path(directory) / "results").write_text("bloqueio", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "persistir"):
                persist_result(self.structured_result(publication), state_path)

            state = state_path.read_text(encoding="utf-8")
            self.assertIn(publication_identity(publication), state)
            self.assertIn('"processed_ids": []', state)

    def test_persists_discard_and_does_not_offer_publication_again(self) -> None:
        rss = (FIXTURES / "openai-news.xml").read_text(encoding="utf-8")

        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            publication = next_publication(rss, state_path)
            self.assertIsNotNone(publication)
            assert publication is not None
            identity = publication_identity(publication)
            decision = {
                "id": identity,
                "title": publication.title,
                "source": "OpenAI News",
                "published_at": publication.published_at.isoformat(),
                "categories": list(publication.categories),
                "original_url": publication.url,
                "relevant": False,
                "related_topics": [],
                "reason": "Conteúdo sem relação útil com os temas do RadarIA.",
            }

            persisted_path = persist_discard(
                decision,
                state_path,
                datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc),
            )

            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertTrue(persisted_path.exists())
            self.assertIn(identity, state["discarded_ids"])
            self.assertIsNone(state["pending_id"])
            self.assertIsNone(next_publication(rss, state_path))
            self.assertFalse((Path(directory) / "results").exists())

    def test_deduplicates_active_candidate_by_canonical_url(self) -> None:
        publication = Publication(
            title="Item visto no feed",
            url="https://example.com/articles/item/?tracking=1",
            published_at=datetime(2026, 9, 5, tzinfo=timezone.utc),
            categories=(),
            guid="opaque-feed-guid",
            source="Fonte primária",
        )
        candidate = {
            "title": publication.title,
            "source": publication.source,
            "published_at": publication.published_at.isoformat(),
            "categories": [],
            "original_url": "https://example.com/articles/item#section",
        }
        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            remember_publication_urls([publication], state_path)

            result = register_active_candidate(candidate, state_path)
            state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "already_known")
        self.assertIsNone(state["pending_id"])

    def test_registers_new_active_candidate_in_existing_pending_flow(self) -> None:
        candidate = {
            "title": "Novidade descoberta ativamente",
            "source": "Fonte primária",
            "published_at": "2026-09-06T10:00:00+00:00",
            "categories": ["Agents"],
            "original_url": "https://example.com/new-agent-technique/",
        }
        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"

            result = register_active_candidate(candidate, state_path)
            state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "candidate_registered")
        self.assertEqual(
            state["pending_id"], "url:https://example.com/new-agent-technique"
        )

    def test_deduplicates_active_url_against_equivalent_feed_guid(self) -> None:
        candidate = {
            "title": "Item já coberto pelo feed",
            "source": "Fonte primária",
            "published_at": "2026-09-06T10:00:00+00:00",
            "categories": [],
            "original_url": "https://example.com/feed-item/",
        }
        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "initialized": True,
                        "baseline_ids": [],
                        "processed_ids": [],
                        "discarded_ids": [],
                        "source_baselines": {
                            "official_feed": ["guid:https://example.com/feed-item"]
                        },
                        "known_urls": [],
                        "pending_id": None,
                    }
                ),
                encoding="utf-8",
            )

            result = register_active_candidate(candidate, state_path)

        self.assertEqual(result["status"], "already_known")


class ConsumptionOutputTests(unittest.TestCase):
    @staticmethod
    def persisted_record(identity: str, title: str, published_at: str) -> dict:
        return {
            "schema_version": 1,
            "id": identity,
            "title": title,
            "source": "OpenAI News",
            "published_at": published_at,
            "categories": ["Research"],
            "objective_summary": f"Resumo de {title}.",
            "main_novelty": f"Novidade de {title}.",
            "relevance_reason": f"Relevância de {title}.",
            "original_url": f"https://openai.com/index/{identity}",
            "processed_at": "2026-09-04T12:00:00+00:00",
        }

    def test_generates_valid_empty_collection(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output_path = root / "public" / "radaria.json"

            payload = build_consumption_output(root / ".radaria" / "state.json", output_path)

            self.assertEqual(payload, {"contract_version": "1.0", "items": []})
            self.assertEqual(
                json.loads(output_path.read_text(encoding="utf-8")), payload
            )

    def test_maps_only_consumer_fields_and_orders_newest_first(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / ".radaria" / "state.json"
            results_path = state_path.parent / "results"
            results_path.mkdir(parents=True)
            older = self.persisted_record("older", "Antiga", "2026-09-01T09:00:00+00:00")
            newer = self.persisted_record("newer", "Recente", "2026-09-03T09:00:00+00:00")
            (results_path / "older.json").write_text(
                json.dumps(older), encoding="utf-8"
            )
            (results_path / "newer.json").write_text(
                json.dumps(newer), encoding="utf-8"
            )

            payload = build_consumption_output(state_path, root / "output" / "radaria.json")

            self.assertEqual([item["id"] for item in payload["items"]], ["newer", "older"])
            self.assertEqual(
                set(payload["items"][0]),
                {
                    "id",
                    "titulo",
                    "fonte",
                    "data",
                    "categoria",
                    "resumo",
                    "novidade_principal",
                    "relevancia",
                    "url",
                    "processado_em",
                },
            )


if __name__ == "__main__":
    unittest.main()
