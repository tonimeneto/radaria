"""Prova funcional de coleta do item mais recente do OpenAI News."""

from __future__ import annotations

import json
import hashlib
import re
import sys
import textwrap
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse


RSS_URL = "https://openai.com/news/rss.xml"
ANTHROPIC_NEWS_URL = "https://www.anthropic.com/news"
DEEPMIND_RSS_URL = "https://deepmind.google/blog/rss.xml"
GOOGLE_DEVELOPERS_RSS_URL = "https://developers.googleblog.com/rss/"
GITHUB_COPILOT_BLOG_RSS_URL = "https://github.blog/ai-and-ml/github-copilot/feed/"
GITHUB_COPILOT_CHANGELOG_RSS_URL = (
    "https://github.blog/changelog/label/copilot/feed/"
)
USER_AGENT = "RadarIA/0.1 (+local functional proof)"
TIMEOUT_SECONDS = 30
STATE_PATH = Path(__file__).resolve().parent / ".radaria" / "state.json"
STATE_VERSION = 1
RESULT_SCHEMA_VERSION = 2
DISCARD_SCHEMA_VERSION = 1
OUTPUT_CONTRACT_VERSION = "2.0"
OUTPUT_PATH = Path(__file__).resolve().parent / "output" / "radaria.json"


@dataclass(frozen=True)
class Publication:
    title: str
    url: str
    published_at: datetime
    categories: tuple[str, ...]
    guid: str = ""
    source: str = "OpenAI News"


@dataclass(frozen=True)
class FeedEntry:
    title: str
    url: str
    guid: str


def fetch(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/xml, text/html;q=0.9",
        },
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def is_openai_editorial_page(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() in {"openai.com", "www.openai.com"}
        and parsed.path.startswith("/index/")
    )


def editorial_publications(rss: str) -> list[Publication]:
    root = ET.fromstring(rss)
    publications: list[Publication] = []

    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or "").strip()
        raw_date = (item.findtext("pubDate") or "").strip()

        if not title or not raw_date or not is_openai_editorial_page(url):
            continue

        try:
            published_at = parsedate_to_datetime(raw_date)
        except (TypeError, ValueError):
            continue

        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)

        categories = tuple(
            category.text.strip()
            for category in item.findall("category")
            if category.text and category.text.strip()
        )
        publications.append(
            Publication(
                title,
                url,
                published_at.astimezone(timezone.utc),
                categories,
                guid,
            )
        )

    if not publications:
        raise RuntimeError("O RSS não contém páginas editoriais válidas da OpenAI.")

    return publications


def latest_editorial_publication(rss: str) -> Publication:
    publications = editorial_publications(rss)

    return max(publications, key=lambda publication: publication.published_at)


def rss_feed_entries(rss: str, allowed_hosts: set[str]) -> list[FeedEntry]:
    root = ET.fromstring(rss)
    entries: list[FeedEntry] = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or "").strip()
        if (
            title
            and url
            and guid
            and (urlparse(url).hostname or "").lower() in allowed_hosts
        ):
            entries.append(FeedEntry(title, url, guid))
    if not entries:
        raise RuntimeError("O feed oficial não contém publicações válidas.")
    return entries


def feed_entry_identity(entry: FeedEntry) -> str:
    return f"guid:{normalize_url(entry.guid, preserve_query=True)}"


def dated_rss_publications(
    rss: str,
    allowed_hosts: set[str],
    source: str,
) -> list[Publication]:
    root = ET.fromstring(rss)
    publications: list[Publication] = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or "").strip()
        raw_date = (item.findtext("pubDate") or "").strip()
        if (
            not title
            or not guid
            or not raw_date
            or (urlparse(url).hostname or "").lower() not in allowed_hosts
        ):
            continue
        try:
            published_at = parsedate_to_datetime(raw_date)
        except (TypeError, ValueError):
            continue
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        categories = tuple(
            category.text.strip()
            for category in item.findall("category")
            if category.text and category.text.strip()
        )
        publications.append(
            Publication(
                title=title,
                url=url,
                published_at=published_at.astimezone(timezone.utc),
                categories=categories,
                guid=guid,
                source=source,
            )
        )
    if not publications:
        raise RuntimeError(f"O RSS de {source} não contém publicações válidas.")
    return publications


def deepmind_publications(rss: str) -> list[Publication]:
    return dated_rss_publications(rss, {"deepmind.google"}, "Google DeepMind")


def google_developer_publication(entry: FeedEntry, html: str) -> Publication:
    date_match = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html)
    if not date_match:
        raise RuntimeError(
            "A publicação do Google Developers Blog não informa datePublished."
        )
    published_at = _parse_iso_datetime(date_match.group(1), "datePublished")
    return Publication(
        title=entry.title,
        url=entry.url,
        published_at=published_at,
        categories=(),
        guid=entry.guid,
        source="Google Developers Blog",
    )


def initialize_source_baselines(
    baseline_inputs: dict[str, list[str]],
    state_path: Path = STATE_PATH,
) -> dict:
    state = load_state(state_path)
    source_baselines = state.setdefault("source_baselines", {})
    initialized_source = False
    for source_key, identities in baseline_inputs.items():
        if source_key not in source_baselines:
            source_baselines[source_key] = sorted(set(identities))
            initialized_source = True
    if initialized_source:
        save_state(state, state_path)
    return state


def google_publications(
    deepmind_rss: str,
    developers_rss: str,
    state_path: Path = STATE_PATH,
) -> list[Publication]:
    deepmind = deepmind_publications(deepmind_rss)
    developer_entries = rss_feed_entries(
        developers_rss, {"developers.googleblog.com"}
    )
    remember_urls([entry.url for entry in developer_entries], state_path)
    baseline_inputs = {
        "google_deepmind": [publication_identity(item) for item in deepmind],
        "google_developers_blog": [
            feed_entry_identity(item) for item in developer_entries
        ],
    }
    state = initialize_source_baselines(baseline_inputs, state_path)

    known_ids = set(state.get("processed_ids", [])) | set(
        state.get("discarded_ids", [])
    )
    for identities in state["source_baselines"].values():
        known_ids.update(identities)

    new_developer_entries = [
        entry
        for entry in developer_entries
        if feed_entry_identity(entry) not in known_ids
    ]
    developer_publications = [
        google_developer_publication(entry, fetch(entry.url))
        for entry in new_developer_entries
    ]
    return deepmind + developer_publications


def github_publications(
    copilot_blog_rss: str,
    copilot_changelog_rss: str,
    state_path: Path = STATE_PATH,
) -> list[Publication]:
    copilot_blog = dated_rss_publications(
        copilot_blog_rss, {"github.blog"}, "GitHub Copilot Blog"
    )
    copilot_changelog = dated_rss_publications(
        copilot_changelog_rss, {"github.blog"}, "GitHub Copilot Changelog"
    )
    initialize_source_baselines(
        {
            "github_copilot_blog": [
                publication_identity(item) for item in copilot_blog
            ],
            "github_copilot_changelog": [
                publication_identity(item) for item in copilot_changelog
            ],
        },
        state_path,
    )
    return copilot_blog + copilot_changelog


class AnthropicNewsParser(HTMLParser):
    HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[dict] = []
        self.open_anchors: list[dict] = []
        self.tag_stack: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = dict(attrs)
        self.tag_stack.append((tag, attributes))
        if tag == "a":
            self.open_anchors.append(
                {
                    "href": attributes.get("href") or "",
                    "headings": [],
                    "times": [],
                    "spans": [],
                    "subjects": [],
                    "titles": [],
                }
            )

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "a" and self.open_anchors:
            self.anchors.append(self.open_anchors.pop())
        open_tags = [open_tag for open_tag, _ in self.tag_stack]
        if tag in open_tags:
            reverse_index = open_tags[::-1].index(tag)
            del self.tag_stack[len(self.tag_stack) - reverse_index - 1 :]

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text or not self.open_anchors or not self.tag_stack:
            return
        current_tag, attributes = self.tag_stack[-1]
        target = None
        if current_tag in self.HEADING_TAGS:
            target = "headings"
        elif current_tag == "time":
            target = "times"
        elif current_tag == "span":
            classes = attributes.get("class") or ""
            if "__title" in classes:
                target = "titles"
            elif "__subject" in classes:
                target = "subjects"
            else:
                target = "spans"
        if target:
            for anchor in self.open_anchors:
                anchor[target].append(text)


def is_anthropic_publication_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() in {"anthropic.com", "www.anthropic.com"}
        and parsed.path not in {"", "/", "/news", "/news/"}
    )


def anthropic_publications(html: str) -> list[Publication]:
    parser = AnthropicNewsParser()
    parser.feed(html)
    parser.close()
    publications: dict[str, Publication] = {}

    for anchor in parser.anchors:
        discovered_url = urlparse(urljoin(ANTHROPIC_NEWS_URL, anchor["href"]))
        url = discovered_url._replace(query="", fragment="").geturl().rstrip("/")
        if (
            not is_anthropic_publication_url(url)
            or not (anchor["headings"] or anchor["titles"])
            or not anchor["times"]
        ):
            continue
        try:
            published_at = datetime.strptime(
                anchor["times"][0], "%b %d, %Y"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        category_values = anchor["subjects"][:1] or anchor["spans"][:1]
        categories = tuple(dict.fromkeys(category_values))
        title_parts = anchor["headings"] or anchor["titles"]
        publications[url] = Publication(
            title=" ".join(title_parts[0].split()),
            url=url,
            published_at=published_at,
            categories=categories,
            source="Anthropic News",
        )

    if not publications:
        raise RuntimeError("A página Anthropic News não contém publicações válidas.")
    return list(publications.values())


def normalize_url(value: str, preserve_query: bool = False) -> str:
    parsed = urlparse(value.strip())
    if not parsed.scheme or not parsed.hostname:
        return value.strip()
    hostname = parsed.hostname.lower()
    if hostname in {"www.openai.com", "www.anthropic.com"}:
        hostname = hostname.removeprefix("www.")
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path.rstrip("/") or "/"
    return parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=f"{hostname}{port}",
        path=path,
        params="",
        query=parsed.query if preserve_query and path == "/" else "",
        fragment="",
    ).geturl()


def publication_identity(publication: Publication) -> str:
    if publication.guid:
        return f"guid:{normalize_url(publication.guid, preserve_query=True)}"
    return f"url:{normalize_url(publication.url)}"


def empty_state() -> dict:
    return {
        "version": STATE_VERSION,
        "initialized": False,
        "baseline_ids": [],
        "processed_ids": [],
        "discarded_ids": [],
        "source_baselines": {},
        "known_urls": [],
        "pending_id": None,
    }


def load_state(path: Path = STATE_PATH) -> dict:
    if not path.exists():
        return empty_state()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Não foi possível ler o estado em {path}.") from error
    if state.get("version") != STATE_VERSION:
        raise RuntimeError(f"Versão de estado incompatível em {path}.")
    state.setdefault("discarded_ids", [])
    state.setdefault("source_baselines", {})
    state.setdefault("known_urls", [])
    return state


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


def remember_urls(urls: list[str], path: Path = STATE_PATH) -> None:
    state = load_state(path)
    known_urls = set(state.get("known_urls", []))
    updated_urls = known_urls | {normalize_url(url) for url in urls}
    if updated_urls != known_urls:
        state["known_urls"] = sorted(updated_urls)
        save_state(state, path)


def remember_publication_urls(
    publications: list[Publication], path: Path = STATE_PATH
) -> None:
    remember_urls([publication.url for publication in publications], path)


def register_active_candidate(candidate: dict, path: Path = STATE_PATH) -> dict:
    string_fields = ("title", "source", "published_at", "original_url")
    if any(
        not isinstance(candidate.get(field), str) or not candidate[field].strip()
        for field in string_fields
    ):
        raise RuntimeError("O candidato da busca ativa está incompleto.")
    categories = candidate.get("categories")
    if not isinstance(categories, list) or any(
        not isinstance(category, str) or not category.strip()
        for category in categories
    ):
        raise RuntimeError("As categorias do candidato são inválidas.")
    _parse_iso_datetime(candidate["published_at"], "published_at")
    normalized_url = normalize_url(candidate["original_url"])
    parsed_url = urlparse(normalized_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
        raise RuntimeError("A URL do candidato é inválida.")

    identity = f"url:{normalized_url}"
    state = load_state(path)
    known_ids = set(state.get("baseline_ids", [])) | set(
        state.get("processed_ids", [])
    ) | set(state.get("discarded_ids", []))
    for identities in state.get("source_baselines", {}).values():
        known_ids.update(identities)
    equivalent_guid = f"guid:{normalized_url}"
    if (
        identity in known_ids
        or equivalent_guid in known_ids
        or normalized_url in state.get("known_urls", [])
    ):
        if state.get("pending_id") == identity:
            state["pending_id"] = None
            save_state(state, path)
        return {"status": "already_known", "identity": identity}

    pending_id = state.get("pending_id")
    if pending_id and pending_id != identity:
        raise RuntimeError("Já existe outra publicação pendente.")

    state["pending_id"] = identity
    save_state(state, path)
    return {"status": "candidate_registered", "identity": identity}


def next_from_publications(
    publications: list[Publication], path: Path = STATE_PATH
) -> Publication | None:
    state = load_state(path)
    pending_id = state.get("pending_id")

    if pending_id:
        pending = next(
            (
                publication
                for publication in publications
                if publication_identity(publication) == pending_id
            ),
            None,
        )
        if pending is None:
            raise RuntimeError("A publicação pendente não está mais presente no RSS.")
        return pending

    known_ids = set(state.get("baseline_ids", [])) | set(
        state.get("processed_ids", [])
    ) | set(state.get("discarded_ids", []))
    for identities in state.get("source_baselines", {}).values():
        known_ids.update(identities)
    candidates = [
        publication
        for publication in publications
        if publication_identity(publication) not in known_ids
    ]
    if not candidates:
        return None

    selected = max(candidates, key=lambda publication: publication.published_at)
    selected_id = publication_identity(selected)
    if not state.get("initialized"):
        state["baseline_ids"] = sorted(
            publication_identity(publication)
            for publication in publications
            if publication_identity(publication) != selected_id
        )
        state["initialized"] = True
    state["pending_id"] = selected_id
    save_state(state, path)
    return selected


def next_publication(rss: str, path: Path = STATE_PATH) -> Publication | None:
    return next_from_publications(editorial_publications(rss), path)


def mark_processed(identity: str, path: Path = STATE_PATH) -> None:
    state = load_state(path)
    if state.get("pending_id") != identity:
        raise RuntimeError("O identificador não corresponde à publicação pendente.")
    processed_ids = set(state.get("processed_ids", []))
    processed_ids.add(identity)
    state["processed_ids"] = sorted(processed_ids)
    state["pending_id"] = None
    save_state(state, path)


def mark_discarded(identity: str, path: Path = STATE_PATH) -> None:
    state = load_state(path)
    if state.get("pending_id") != identity:
        raise RuntimeError("O identificador não corresponde à publicação pendente.")
    discarded_ids = set(state.get("discarded_ids", []))
    discarded_ids.add(identity)
    state["discarded_ids"] = sorted(discarded_ids)
    state["pending_id"] = None
    save_state(state, path)


def result_path(identity: str, state_path: Path = STATE_PATH) -> Path:
    filename = hashlib.sha256(identity.encode("utf-8")).hexdigest() + ".json"
    return state_path.parent / "results" / filename


def discard_path(identity: str, state_path: Path = STATE_PATH) -> Path:
    filename = hashlib.sha256(identity.encode("utf-8")).hexdigest() + ".json"
    return state_path.parent / "discards" / filename


def _parse_iso_datetime(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"Data inválida no campo {field}.") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_consumption_output(
    state_path: Path = STATE_PATH,
    output_path: Path = OUTPUT_PATH,
) -> dict:
    results_directory = state_path.parent / "results"
    records: list[dict] = []
    if results_directory.exists():
        for persisted_path in results_directory.glob("*.json"):
            try:
                record = json.loads(persisted_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise RuntimeError(
                    f"Não foi possível ler o resultado persistido em {persisted_path}."
                ) from error
            validate_persisted_record(record)
            records.append(record)

    records.sort(
        key=lambda record: _parse_iso_datetime(record["published_at"], "published_at"),
        reverse=True,
    )
    payload = {
        "contract_version": OUTPUT_CONTRACT_VERSION,
        "items": [
            {
                "id": record["id"],
                "titulo": record["title"],
                "fonte": record["source"],
                "data": record["published_at"],
                "categoria": record["categories"],
                "resumo": record["objective_summary"],
                "novidade_principal": record["main_novelty"],
                "relevancia": record["relevance_reason"],
                "url": record["original_url"],
                "processado_em": record["processed_at"],
            }
            for record in records
        ],
    }

    temporary_path = output_path.with_suffix(".tmp")
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(output_path)
    except OSError as error:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError("Não foi possível gerar a saída de consumo.") from error
    return payload


def validate_persisted_record(record: dict) -> None:
    if record.get("schema_version") != RESULT_SCHEMA_VERSION:
        raise RuntimeError("Versão incompatível em resultado persistido.")
    validate_result(
        {
            "id": record.get("id"),
            "title": record.get("title"),
            "source": record.get("source"),
            "published_at": record.get("published_at"),
            "categories": record.get("categories"),
            "objective_summary": record.get("objective_summary"),
            "main_novelty": record.get("main_novelty"),
            "relevance_reason": record.get("relevance_reason"),
            "original_url": record.get("original_url"),
        }
    )
    if not isinstance(record.get("processed_at"), str) or not record["processed_at"].strip():
        raise RuntimeError("O resultado persistido não contém data de processamento.")
    _parse_iso_datetime(record["processed_at"], "processed_at")


def validate_result(result: dict) -> None:
    string_fields = (
        "id",
        "source",
        "published_at",
        "original_url",
    )
    if any(
        not isinstance(result.get(field), str) or not result[field].strip()
        for field in string_fields
    ):
        raise RuntimeError("O resultado estruturado está incompleto.")
    for field in ("title", "objective_summary", "main_novelty", "relevance_reason"):
        localized = result.get(field)
        if not isinstance(localized, dict) or set(localized) != {"pt", "en"}:
            raise RuntimeError(f"O campo bilíngue {field} é inválido.")
        if any(
            not isinstance(localized.get(language), str)
            or not localized[language].strip()
            for language in ("pt", "en")
        ):
            raise RuntimeError(f"O campo bilíngue {field} está incompleto.")

    categories = result.get("categories")
    if not isinstance(categories, dict) or set(categories) != {"pt", "en"}:
        raise RuntimeError("As categorias bilíngues do resultado são inválidas.")
    for language in ("pt", "en"):
        values = categories[language]
        if not isinstance(values, list) or any(
            not isinstance(category, str) or not category.strip()
            for category in values
        ):
            raise RuntimeError("As categorias bilíngues do resultado são inválidas.")


def validate_discard(decision: dict) -> None:
    string_fields = (
        "id",
        "title",
        "source",
        "published_at",
        "reason",
        "original_url",
    )
    if any(
        not isinstance(decision.get(field), str) or not decision[field].strip()
        for field in string_fields
    ):
        raise RuntimeError("A decisão de descarte está incompleta.")
    if decision.get("relevant") is not False:
        raise RuntimeError("Somente decisões irrelevantes podem ser descartadas.")
    for field in ("categories", "related_topics"):
        values = decision.get(field)
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value.strip() for value in values
        ):
            raise RuntimeError(f"O campo {field} da decisão é inválido.")


def persist_discard(
    decision: dict,
    state_path: Path = STATE_PATH,
    analyzed_at: datetime | None = None,
) -> Path:
    validate_discard(decision)
    identity = decision["id"].strip()
    state = load_state(state_path)
    is_pending = state.get("pending_id") == identity
    is_already_discarded = identity in state.get("discarded_ids", [])
    if not is_pending and not is_already_discarded:
        raise RuntimeError("O descarte não corresponde a uma publicação conhecida.")

    destination = discard_path(identity, state_path)
    if not destination.exists():
        record = {
            "schema_version": DISCARD_SCHEMA_VERSION,
            "id": identity,
            "title": decision["title"].strip(),
            "source": decision["source"].strip(),
            "published_at": decision["published_at"].strip(),
            "categories": [value.strip() for value in decision["categories"]],
            "original_url": decision["original_url"].strip(),
            "relevant": False,
            "related_topics": [
                value.strip() for value in decision["related_topics"]
            ],
            "reason": decision["reason"].strip(),
            "analyzed_at": (analyzed_at or datetime.now(timezone.utc)).isoformat(),
        }
        temporary_path = destination.with_suffix(".tmp")
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary_path.replace(destination)
        except OSError as error:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise RuntimeError("Não foi possível persistir o descarte.") from error

    if is_pending:
        mark_discarded(identity, state_path)
    return destination


def persist_result(
    result: dict,
    state_path: Path = STATE_PATH,
    processed_at: datetime | None = None,
    output_path: Path | None = None,
) -> Path:
    validate_result(result)
    identity = result["id"].strip()
    state = load_state(state_path)
    is_pending = state.get("pending_id") == identity
    is_already_processed = identity in state.get("processed_ids", [])
    if not is_pending and not is_already_processed:
        raise RuntimeError("O resultado não corresponde a uma publicação conhecida.")

    destination = result_path(identity, state_path)
    if not destination.exists():
        record = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "id": identity,
            "title": {language: result["title"][language].strip() for language in ("pt", "en")},
            "source": result["source"].strip(),
            "published_at": result["published_at"].strip(),
            "categories": {language: [category.strip() for category in result["categories"][language]] for language in ("pt", "en")},
            "objective_summary": {language: result["objective_summary"][language].strip() for language in ("pt", "en")},
            "main_novelty": {language: result["main_novelty"][language].strip() for language in ("pt", "en")},
            "relevance_reason": {language: result["relevance_reason"][language].strip() for language in ("pt", "en")},
            "original_url": result["original_url"].strip(),
            "processed_at": (processed_at or datetime.now(timezone.utc)).isoformat(),
        }
        temporary_path = destination.with_suffix(".tmp")
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary_path.replace(destination)
        except OSError as error:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise RuntimeError("Não foi possível persistir o resultado estruturado.") from error

    build_consumption_output(
        state_path,
        output_path
        or (OUTPUT_PATH if state_path == STATE_PATH else state_path.parent / "output.json"),
    )
    if is_pending:
        mark_processed(identity, state_path)
    return destination


def print_latest_post(payload: dict) -> None:
    items = payload["items"]
    if not items:
        print("RadarIA — nenhuma novidade disponível para consumo.")
        return
    item = items[0]
    categories = ", ".join(item["categoria"]["pt"]) or "Não informada"
    print("RadarIA — item mais recente")
    print("=" * 28)
    print(f"Título: {item['titulo']['pt']}")
    print(f"Fonte: {item['fonte']}")
    print(f"Data: {item['data']}")
    print(f"Categoria: {categories}")
    print(f"Resumo: {item['resumo']['pt']}")
    print(f"Principal novidade: {item['novidade_principal']['pt']}")
    print(f"Relevância: {item['relevancia']['pt']}")
    print(f"Link original: {item['url']}")


class ArticleTextExtractor(HTMLParser):
    BLOCK_TAGS = {"h1", "h2", "h3", "h4", "p", "li", "blockquote", "figcaption"}
    EXCLUDED_TAGS = {"nav", "footer", "button", "script", "style", "svg", "template"}
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self, root_tag: str = "article", root_class: str | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self.root_tag = root_tag
        self.root_class = root_class
        self.article_depth = 0
        self.excluded_depth = 0
        self.capture_tag: str | None = None
        self.capture_depth = 0
        self.capture_parts: list[str] = []
        self.blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = dict(attrs)
        if not self.article_depth:
            classes = (attributes.get("class") or "").split()
            if tag == self.root_tag and (
                self.root_class is None or self.root_class in classes
            ):
                self.article_depth = 1
            return
        if tag not in self.VOID_TAGS:
            self.article_depth += 1
        if self.excluded_depth:
            if tag not in self.VOID_TAGS:
                self.excluded_depth += 1
            return
        classes = (attributes.get("class") or "").split()
        if (
            tag in self.EXCLUDED_TAGS
            or "sr-only" in classes
            or attributes.get("data-testid") == "citations"
        ):
            self.excluded_depth += 1
            return
        if self.capture_tag is None and tag in self.BLOCK_TAGS:
            self.capture_tag = tag
            self.capture_depth = 1
            self.capture_parts = []
        elif self.capture_tag is not None:
            self.capture_depth += 1
        if tag == "br" and self.capture_tag is not None:
            self.capture_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if not self.article_depth:
            return
        if self.excluded_depth:
            self.excluded_depth -= 1
        elif self.capture_tag is not None:
            self.capture_depth -= 1
            if tag == self.capture_tag or self.capture_depth == 0:
                text = " ".join("".join(self.capture_parts).split())
                if text:
                    self.blocks.append(text)
                self.capture_tag = None
                self.capture_depth = 0
                self.capture_parts = []
        self.article_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.article_depth and not self.excluded_depth and self.capture_tag is not None:
            self.capture_parts.append(data)


def extract_editorial_content(
    html: str,
    title: str,
    stop_markers: tuple[str, ...] = ("keep reading",),
    root_tag: str = "article",
    root_class: str | None = None,
) -> list[str]:
    parser = ArticleTextExtractor(root_tag, root_class)
    parser.feed(html)
    parser.close()

    if not parser.blocks:
        raise RuntimeError("Nenhum conteúdo editorial foi encontrado no elemento <article>.")

    content: list[str] = []
    for block in parser.blocks:
        if block.casefold() in stop_markers:
            break
        if block != title and (not content or content[-1] != block):
            content.append(block)

    if not content:
        raise RuntimeError("O artigo foi encontrado, mas não produziu conteúdo textual.")
    return content


def extract_anthropic_structured_content(html: str, title: str) -> list[str]:
    serialized_chunks = re.findall(
        r'self\.__next_f\.push\(\[1,"((?:\\.|[^"\\])*)"\]\)</script>',
        html,
    )
    decoded_chunks: list[str] = []
    for chunk in serialized_chunks:
        try:
            decoded_chunks.append(json.loads(f'"{chunk}"'))
        except json.JSONDecodeError:
            continue
    payload = "".join(decoded_chunks)
    marker = '"chapters":'
    marker_position = payload.find(marker)
    if marker_position < 0:
        raise RuntimeError("O conteúdo estruturado da publicação não foi encontrado.")
    try:
        chapters, _ = json.JSONDecoder().raw_decode(
            payload[marker_position + len(marker) :]
        )
    except json.JSONDecodeError as error:
        raise RuntimeError("O conteúdo estruturado da publicação é inválido.") from error

    blocks: list[str] = []

    def collect(value: object) -> None:
        if isinstance(value, list):
            for item in value:
                collect(item)
            return
        if not isinstance(value, dict):
            return
        if value.get("_type") == "block" and isinstance(value.get("children"), list):
            text = "".join(
                child.get("text", "")
                for child in value["children"]
                if isinstance(child, dict) and isinstance(child.get("text"), str)
            )
            normalized = " ".join(text.split())
            if normalized and normalized != title:
                blocks.append(normalized)
            return
        if value.get("_type") == "historySlackBlock" and isinstance(
            value.get("message"), str
        ):
            normalized = " ".join(value["message"].split())
            if normalized:
                blocks.append(normalized)
            return
        for nested in value.values():
            collect(nested)

    collect(chapters)
    if not blocks:
        raise RuntimeError("A publicação não produziu conteúdo editorial estruturado.")
    return blocks


def content_for_analysis(content: list[str]) -> str:
    date_pattern = re.compile(r"^[A-Z][a-z]+ \d{1,2}, \d{4}$")
    relevant_blocks = [
        block
        for block in content
        if not date_pattern.fullmatch(block) and not block.casefold().startswith("author:")
    ]
    if not relevant_blocks:
        raise RuntimeError("Não há conteúdo editorial suficiente para resumir.")
    return "\n\n".join(relevant_blocks)


def build_collected_publication(publication: Publication, content: list[str]) -> dict:
    return {
        "status": "publication_found",
        "identity": publication_identity(publication),
        "title": publication.title,
        "source": publication.source,
        "published_at": publication.published_at.isoformat(),
        "categories": list(publication.categories),
        "url": publication.url,
        "content": content_for_analysis(content),
    }


def extract_publication_content(
    publication: Publication, article_html: str
) -> list[str]:
    stop_markers = (
        ("keep reading", "related content")
        if publication.source == "Anthropic News"
        else ("keep reading",)
    )
    root_tag = "article"
    root_class = None
    if publication.source == "Google Developers Blog":
        root_tag = "div"
        root_class = "rich-content"
    elif publication.source == "Google DeepMind":
        stop_markers = (
            "keep reading",
            "get the latest news from google in your inbox",
            "related posts",
        )
        try:
            return extract_editorial_content(
                article_html,
                publication.title,
                stop_markers,
                root_tag="div",
                root_class="article-container__content",
            )
        except RuntimeError:
            return extract_editorial_content(
                article_html,
                publication.title,
                stop_markers,
                root_tag="main",
            )
    elif publication.source == "GitHub Copilot Blog":
        root_tag = "section"
        root_class = "post__content"
    elif publication.source == "GitHub Copilot Changelog":
        root_tag = "div"
        root_class = "PostContent-main"
    try:
        return extract_editorial_content(
            article_html,
            publication.title,
            stop_markers,
            root_tag,
            root_class,
        )
    except RuntimeError:
        if publication.source != "Anthropic News":
            raise
        return extract_anthropic_structured_content(article_html, publication.title)


def print_result(publication: Publication, content: list[str]) -> None:
    categories = ", ".join(publication.categories) or "Não informada"
    print("RadarIA — prova funcional OpenAI News")
    print("=" * 39)
    print(f"Título: {publication.title}")
    print(f"URL: {publication.url}")
    print(f"Data (UTC): {publication.published_at.isoformat()}")
    print(f"Categoria(s): {categories}")
    print(f"Blocos editoriais extraídos: {len(content)}")
    print("\nConteúdo editorial\n-------------------")
    for block in content:
        print(textwrap.fill(block, width=100))
        print()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        arguments = sys.argv[1:]
        if arguments == ["--build-output"]:
            payload = build_consumption_output()
            print(f"Saída de consumo gerada em {OUTPUT_PATH}")
            print()
            print_latest_post(payload)
            return 0
        if len(arguments) == 2 and arguments[0] == "--discard-publication":
            input_path = Path(arguments[1])
            try:
                decision = json.loads(input_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise RuntimeError(
                    f"Não foi possível ler a decisão em {input_path}."
                ) from error
            destination = persist_discard(decision)
            print(f"Publicação descartada e registrada em {destination}")
            return 0
        if len(arguments) == 2 and arguments[0] == "--save-result":
            input_path = Path(arguments[1])
            try:
                result = json.loads(input_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise RuntimeError(
                    f"Não foi possível ler o resultado em {input_path}."
                ) from error
            destination = persist_result(result)
            print(f"Resultado persistido em {destination}")
            return 0
        if len(arguments) == 2 and arguments[0] == "--register-candidate":
            input_path = Path(arguments[1])
            try:
                candidate = json.loads(input_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise RuntimeError(
                    f"Não foi possível ler o candidato em {input_path}."
                ) from error
            print(
                json.dumps(
                    register_active_candidate(candidate),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if arguments not in ([], ["--json"]):
            raise RuntimeError(
                "Uso: python radaria.py [--json | --build-output | "
                "--register-candidate ARQUIVO_JSON | "
                "--discard-publication ARQUIVO_JSON | "
                "--save-result ARQUIVO_JSON]"
            )
        openai_rss = fetch(RSS_URL)
        anthropic_news = fetch(ANTHROPIC_NEWS_URL)
        deepmind_rss = fetch(DEEPMIND_RSS_URL)
        google_developers_rss = fetch(GOOGLE_DEVELOPERS_RSS_URL)
        github_copilot_blog_rss = fetch(GITHUB_COPILOT_BLOG_RSS_URL)
        github_copilot_changelog_rss = fetch(GITHUB_COPILOT_CHANGELOG_RSS_URL)
        publications = editorial_publications(openai_rss) + anthropic_publications(
            anthropic_news
        ) + google_publications(
            deepmind_rss, google_developers_rss
        ) + github_publications(
            github_copilot_blog_rss, github_copilot_changelog_rss
        )
        remember_publication_urls(publications)
        publication = next_from_publications(publications)
        if publication is None:
            if "--json" in arguments:
                print(
                    json.dumps(
                        {
                            "status": "no_new_publications",
                            "source": (
                                "OpenAI News + Anthropic News + Google DeepMind + "
                                "Google Developers Blog + GitHub Copilot Blog + "
                                "GitHub Copilot Changelog"
                            ),
                            "message": "Não existem novas publicações para processar.",
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                print("Não existem novas publicações para processar.")
            return 0
        article_html = fetch(publication.url)
        content = extract_publication_content(publication, article_html)
        if "--json" in arguments:
            print(
                json.dumps(
                    build_collected_publication(publication, content),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print_result(publication, content)
        return 0
    except (ET.ParseError, OSError, RuntimeError) as error:
        print(f"Falha na coleta: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
