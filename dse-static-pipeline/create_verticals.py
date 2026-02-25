import json
import os
from argparse import ArgumentParser
from re import match, sub
from time import perf_counter, sleep

import requests
from lxml import etree as ET
from yaml import dump, safe_load

PAR_SEP = "PaRaSeP"


def get_tei_locations(oai_base: str) -> dict:
    """Fetch TEI locations from OAI-PMH endpoint."""
    response = requests.get(f"{oai_base}?verb=ListRecords")
    tree = ET.fromstring(response.content)
    nsmap = {"dc": "http://purl.org/dc/elements/1.1/"}
    ids = tree.xpath("//dc:identifier/text()", namespaces=nsmap)
    titles = tree.xpath("//dc:title/text()", namespaces=nsmap)
    return dict(zip(ids, titles))


def get_paragraph(node):
    """Get parent paragraph element."""
    while (
        node is not None
        and node.tag != "{http://www.tei-c.org/ns/1.0}p"  # noqa
        and node.tag != "{http://www.tei-c.org/ns/1.0}body"  # noqa
    ):
        node = node.getparent()
    return node


def run_udp(text: str, lang: str, cfg: dict, suffix: str = "") -> str:
    """Process text through UDPipe API."""
    data = {
        "data": text,
        "model": cfg["models"][lang],
        "tokenizer": "",
        "tagger": "",
        "output": "conllu",
    }

    response = requests.post(cfg["apiUrl"], files=data)
    result = response.json()["result"]

    vertical = "<p>\n<s>\n"
    for line in result.split("\n"):
        if line.startswith("#") or not line.strip():
            continue

        parts = line.split("\t")
        if len(parts) < 10:
            continue

        item = {"text": parts[1], "lemma": parts[2], "upos": parts[3]}

        if item["text"] == PAR_SEP:
            vertical += "</s>\n</p>\n<p>\n<s>\n"
        else:
            vertical += f"{item['text']}\t{item['lemma']}\t{item['upos']}{suffix}\n"
            if "SpaceAfter=No" in parts[9]:
                vertical += "<g/>\n"

    vertical += "</s>\n</p>\n"
    return vertical


def run_spacy(text: str, lang: str, cfg: dict, suffix: str = "") -> str:
    import spacy

    nlp = spacy.load(cfg["models"][lang])
    doc = nlp(text)
    assert doc.has_annotation("SENT_START")

    vertical = "<p>\n<s>\n"
    sent = None
    for token in doc:
        if token.is_space:
            continue

        # end of sentence recognition
        sent = sent or token.sent
        if sent != token.sent:
            vertical += "</s>\n<s>\n"
        sent = token.sent

        # check if the glue element is needed
        if not match("\\s", text) and token.is_punct:
            vertical += "<g/>\n"
        text = sub("^\\s*", "", text)
        text = text[len(token.text) :]  # noqa E203

        # token itself
        if token.text == PAR_SEP:
            vertical += "</s>\n</p>\n<p>\n<s>\n"
        else:
            vertical += f"{token.text}\t{token.lemma_ if not token.is_punct else token.text}\t{token.pos_}{suffix}\n"

    vertical += "</s>\n</p>\n"
    return vertical


def process_tei(tei_url: str, vertical, corpora: dict, cfg: dict, time: dict) -> bool:
    html_url = tei_url.replace(".xml", ".html")
    t1 = perf_counter()
    response = requests.get(tei_url)
    t2 = perf_counter()

    response.encoding = "utf-8"
    tei_content = response.text
    tei_content = tei_content.replace("<lb/>", " ")
    if "schnitzler-briefe" in tei_url:
        tei_content = tei_content.replace('<c rendition="#langesS">s</c>', "s")
        tei_content = tei_content.replace('<c rendition="gemination-m">mm</c>', "mm")
        tei_content = tei_content.replace('<c rendition="gemination-n">nn</c>', "nn")

    tree = ET.fromstring(tei_content.encode("utf-8"))
    nsmap = {"tei": "http://www.tei-c.org/ns/1.0"}

    vertical.write(f'<chapter ID="{corpora["id"]}" LandingPageURI="{html_url}">\n')

    last_p = None
    text = ""

    for element in tree.xpath(corpora["xpath"], namespaces=nsmap):
        part = element.replace("\n", " ").replace("\r", " ").strip()
        if part == "":
            continue

        p = get_paragraph(element.getparent())
        if last_p is None:
            last_p = p
        if p != last_p:
            text += f" {PAR_SEP} "
            last_p = p
        text += part + " "

    t3 = perf_counter()
    suffix = f"\t{html_url}"
    if cfg["backend"] == "udppipe":
        processed = run_udp(text.strip(), corpora["lang"], cfg["udppipe"], suffix)
    else:
        processed = run_spacy(text.strip(), corpora["lang"], cfg["spacy"], suffix)
    t4 = perf_counter()
    processed = processed.replace("<s>\n</s>\n", "").replace("<p>\n</p>\n", "")
    tokens = processed.count("\n")

    vertical.write(processed)
    vertical.write("</chapter>\n")

    t5 = perf_counter()
    time["download"] += t2 - t1
    time["nlp"] += t4 - t3
    time["tokens"] += tokens
    print(
        f"        dwnld {round(t2 - t1, 2)}s nlp {round(t4 - t3, 2)}s total {round(t5 - t1, 2)}s tokens {tokens}"
    )
    return True


def create_vertical(corpora: dict, output_path: str, cfg: dict):
    """Create vertical file and its config from corpus data."""

    with open(output_path, "w", encoding="utf-8") as vertical:
        vertical.write(f'<doc LandingPageURI="{corpora["landingPage"]}">\n')

        time = {"download": 0.0, "nlp": 0.0, "total": perf_counter(), "tokens": 0}

        N = len(corpora["tei"])
        n = 1
        for tei_url, title in corpora["tei"].items():
            print(f"    {tei_url} ({n}/{N} {round(100 * n / N, 1)}%)")
            for i in range(10):
                try:
                    if process_tei(tei_url, vertical, corpora, cfg, time):
                        break
                except Exception as e:
                    print(f"{e}")
                sleep(5)
            n += 1

        vertical.write("</doc>\n")

        time["total"] = perf_counter() - time["total"]
        time = {k: round(v, 2) for k, v in time.items()}
        print(f"    {time}")
        return True


def create_config(corpora: dict, output_path: str, cfg: dict) -> None:
    with open(output_path, "w") as f:
        # variable part
        f.write(f'MAINTAINER "{cfg["maintainer"]}"\n')
        f.write(f'INFO "{corpora["title"]}"\n')
        f.write(f'NAME "{corpora["id"]}"\n')
        f.write(f'INFOHREF "{corpora["landingPage"]}"\n')
        f.write(f'PATH "{os.path.join(cfg["basePath"]["data"], corpora["id"])}"\n')
        f.write(f'LANGUAGE "{cfg["langMap"][corpora["lang"]]}"\n')
        f.write(
            f'VERTICAL "{os.path.join(cfg["basePath"]["vertical"], os.path.basename(corpora["vertical"]))}"\n'
        )
        # constant part
        f.write(cfg["corporaConfig"])


def create_mquery_sru_config(corpora: dict, output_path: str, cfg: dict) -> None:
    with open(output_path, "w") as f:
        lang = corpora["lang"][0:2]
        # mquery-sru requires an English title to be present
        title = {"en": corpora["title"]}
        title[lang] = corpora["title"]
        data = {
            corpora["id"]: {
                "pid": corpora["pid"],
                "title": title,
                "description": {"en": ""},
                "landingPageURI": corpora["landingPage"],
                "languages": [corpora["lang"]],
                "utterance": "s",
                "paragraph": "p",
                "turn": "p",
                "text": "chapter",
                "session": "chapter",
            }
        }
        dump(data, f)


def main():
    """Main function to process all endpoints."""

    parser = ArgumentParser(
        "Create (No)Sketch Engine verticals from dse-static digital editions"
    )
    parser.add_argument("-l", action="store_true", help="list all availble editions")
    parser.add_argument(
        "-c", default="config.yaml", help="config file to use (default config.yaml)"
    )
    parser.add_argument(
        "-e",
        help="if specified, processes only single digital edition with a specified key",
    )
    parser.add_argument(
        "-s",
        action="store_true",
        help="skip a given digital edition if the vertical file already exists",
    )
    parser.add_argument(
        "--co",
        action="store_true",
        help="create only the config file - useful if only the corpora configuration file template was changed",
    )
    args = parser.parse_args()

    with open(args.c, "r") as f:
        cfg = safe_load(f)
    try:
        response = requests.get(cfg["src"])
        src_data = response.json()["endpoints"]
    except KeyError:
        src_data = {}
    if "staticSrc" in cfg:
        with open(cfg["staticSrc"], "r", encoding="utf-8") as fp:
            src_data.update(json.load(fp))

    if args.l:
        for key in src_data.keys():
            print(key)
        return

    os.makedirs(cfg["outputDir"], exist_ok=True)

    for key, val in src_data.items():
        if args.e and key != args.e:
            continue

        key = sub("[^a-zA-Z0-9]", "", key)
        try:
            teis = val["docs"]
        except KeyError:
            teis = get_tei_locations(val["oai"])

        path_config = os.path.join(cfg["outputDir"], key)
        path_vertical = f"{path_config}.vrt"
        corpora = {
            "id": key,
            "title": val["title"],
            "tei": teis,
            "xpath": val["fulltext_xpath"],
            "landingPage": val["landingpage"],
            "lang": val["default_lang"],
            "pid": val["pid"],
            "vertical": path_vertical,
        }
        print(f"{key}: {corpora['lang']} {len(corpora['tei'])}")

        if os.path.exists(path_vertical) and args.s:
            print(f"    vertical file {path_vertical} already exists - skipping")
            continue
        try:
            create_config(corpora, path_config, cfg)
        except Exception as e:
            print(f"failed to process {key} due to {e}")
            continue
        create_mquery_sru_config(corpora, f"{path_config}.yml", cfg)
        if not args.co:
            create_vertical(corpora, path_vertical, cfg)


if __name__ == "__main__":
    main()
