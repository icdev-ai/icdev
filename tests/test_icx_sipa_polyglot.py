# CUI // SP-CTI
"""icx-sipa-01 -- SIPA finds capabilities in Java, C#, JavaScript/TypeScript and Go, and says what it could not read.

* each language's network / filesystem / process / dynamic-code / crypto / env / serialization / obfuscation APIs
  are found, every record labelled ``method: lexical`` (weaker evidence than Python's AST, and it says so);
* an API named only in a comment is not a capability; one in a string-bearing line still is;
* a language neither extractor reads is UNASSESSED, never clean.
"""
from __future__ import annotations

import textwrap

from tools.integrity import capability_extractor, polyglot_extractor as pg

SAMPLES = {
    "src/Loader.java": """
        import java.net.*;
        class Loader {
            // Runtime.getRuntime().exec("not real: a comment")
            void run() throws Exception {
                Socket s = new Socket("10.0.0.1", 443);
                Process p = Runtime.getRuntime().exec("sh");
                String k = System.getenv("API_KEY");
                Object o = new ObjectInputStream(s.getInputStream()).readObject();
                byte[] b = Base64.getDecoder().decode(k);
                Class.forName("x.Y");
                Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
                Files.write(path, b);
            }
        }
    """,
    "src/Svc.cs": """
        /* Process.Start("comment") */
        class Svc { void Go() {
            var h = new HttpClient();
            Process.Start("cmd.exe");
            var f = new BinaryFormatter();
            File.Delete(@"C:\\\\x");
            var v = Environment.GetEnvironmentVariable("DB");
        } }
    """,
    "web/app.ts": """
        const url = "https://example.org//path";  // a // inside a string is not a comment
        fetch(url);
        const { exec } = require('child_process');
        eval(process.env.CODE);
        fs.writeFileSync('/tmp/x', 'y');
        const blob = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVphYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3h5eg==";
    """,
    "cmd/main.go": """
        package main
        func main() {
            conn, _ := net.Dial("tcp", "10.0.0.1:443")
            exec.Command("sh", "-c", "id").Run()
            os.Getenv("TOKEN")
            os.WriteFile("/tmp/x", nil, 0644)
        }
    """,
    "legacy/PAYROLL.cbl": "       IDENTIFICATION DIVISION.\n",
    "legacy/report.rb": "puts 'hi'\n",
}


def _tree(tmp_path):
    for rel, text in SAMPLES.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text), encoding="utf-8")
    return tmp_path


def _caps(recs, file):
    return {r["capability_type"] for r in recs if r["file_path"].replace("\\", "/") == file}


def test_each_language_yields_the_eight_capability_types(tmp_path):
    recs = pg.extract(str(_tree(tmp_path)))
    assert all(r["evidence"]["method"] == "lexical" and r["risk_weight"] > 0 for r in recs)
    assert _caps(recs, "src/Loader.java") == {"network_egress", "process_exec", "env_secret", "serialization",
                                              "obfuscation", "dynamic_code", "crypto", "filesystem"}
    assert _caps(recs, "src/Svc.cs") == {"network_egress", "process_exec", "serialization", "filesystem", "env_secret"}
    assert _caps(recs, "web/app.ts") == {"network_egress", "process_exec", "dynamic_code", "env_secret", "filesystem",
                                         "obfuscation"}
    assert _caps(recs, "cmd/main.go") == {"network_egress", "process_exec", "env_secret", "filesystem"}


def test_a_comment_is_not_a_capability_and_line_numbers_hold(tmp_path):
    recs = pg.extract(str(_tree(tmp_path)))
    java_exec = [r for r in recs if r["file_path"].endswith("Loader.java") and r["capability_type"] == "process_exec"]
    assert len(java_exec) == 1 and java_exec[0]["line_start"] == 7, "the commented exec on line 4 is not counted"
    cs_exec = [r for r in recs if r["file_path"].endswith("Svc.cs") and r["capability_type"] == "process_exec"]
    assert len(cs_exec) == 1 and cs_exec[0]["line_start"] == 5
    assert pg.strip_comments('a = "x//y"; // gone\nb') == 'a = "x//y"; \nb'


def test_what_sipa_cannot_read_is_unassessed_never_clean(tmp_path):
    cov = pg.coverage(str(_tree(tmp_path)))
    assert cov["unassessed"] == {".cbl": 1, ".rb": 1}
    assert cov["assessed"] == {"csharp": 1, "go": 1, "java": 1, "typescript": 1}
    assert cov["methods"]["python"] == "ast" and cov["methods"]["java"] == "lexical"


def test_the_sipa_manifest_now_includes_other_languages(tmp_path):
    (tmp_path / "tool.py").write_text("import os\nos.system('id')\n", encoding="utf-8")
    recs = capability_extractor.extract(str(_tree(tmp_path)))
    langs = {r["evidence"].get("language", "python") for r in recs}
    assert {"python", "java", "csharp", "typescript", "go"} <= langs
