"""Typed memos are generated in memory and extracted without a model call."""

from io import BytesIO

import pytest
from docx import Document
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen.canvas import Canvas

from mothusi_intake import MAX_MEMO_BYTES, memo_text


def pdf_bytes(*pages: str) -> bytes:
    stream = BytesIO()
    canvas = Canvas(stream)
    for text in pages:
        if text:
            canvas.drawString(72, 720, text)
        else:
            canvas.rect(72, 600, 200, 100)
        canvas.showPage()
    canvas.save()
    return stream.getvalue()


def docx_bytes(*paragraphs: str) -> bytes:
    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


@pytest.mark.parametrize("filename", ["memo.pdf", "memo.PdF"])
def test_pdf_text_is_in_page_order(filename: str) -> None:
    data = pdf_bytes("  Question one: answer is 42.  ", "  Question two: answer is 56.  ")
    assert memo_text(data, filename) == (
        "Question one: answer is 42.\n\nQuestion two: answer is 56."
    )


@pytest.mark.parametrize("filename", ["memo.docx", "memo.DoCx"])
def test_docx_paragraphs_and_table_rows_stay_in_body_order(filename: str) -> None:
    document = Document()
    document.add_paragraph("  Memo: answers and allocated marks  ")
    document.add_paragraph("  Before the table  ")
    table = document.add_table(rows=2, cols=3)
    for row, values in zip(table.rows, [("a", "b", "c"), ("1", "2", "3")], strict=True):
        for cell, value in zip(row.cells, values, strict=True):
            cell.text = value
    document.add_paragraph("  After the table  ")
    stream = BytesIO()
    document.save(stream)
    assert memo_text(stream.getvalue(), filename) == (
        "Memo: answers and allocated marks\nBefore the table\na | b | c\n1 | 2 | 3\nAfter the table"
    )


def test_whitespace_is_normalised_without_changing_internal_spacing() -> None:
    data = docx_bytes(
        "  ",
        "  First  answer: x = 42  ",
        "",
        " \t ",
        "",
        "",
        "  Second answer: x = 56\n  And working  ",
        "",
    )
    assert memo_text(data, "memo.docx") == (
        "First  answer: x = 42\n\nSecond answer: x = 56\nAnd working"
    )


def test_docx_merged_cells_are_emitted_once_without_deduplicating_equal_text() -> None:
    document = Document()
    document.add_paragraph("Memo with horizontal and vertical merges")
    table = document.add_table(rows=4, cols=3)
    table.cell(0, 0).merge(table.cell(0, 2)).text = "Total 10 marks"
    table.cell(1, 0).merge(table.cell(2, 0)).text = "Shared marking instruction"
    table.cell(1, 1).text = "First answer"
    table.cell(1, 2).text = "5 marks"
    table.cell(2, 1).text = "Second answer"
    table.cell(2, 2).text = "5 marks"
    for cell in table.rows[3].cells:
        cell.text = "Same answer"
    stream = BytesIO()
    document.save(stream)
    assert memo_text(stream.getvalue(), "merged.docx") == (
        "Memo with horizontal and vertical merges\nTotal 10 marks\n"
        "Shared marking instruction | First answer | 5 marks\n"
        "Second answer | 5 marks\nSame answer | Same answer | Same answer"
    )


def test_docx_nested_tables_emit_rows_on_their_own_lines_recursively() -> None:
    document = Document()
    document.add_paragraph("Memo before the outer table")
    outer = document.add_table(rows=1, cols=2)
    cell = outer.cell(0, 0)
    cell.text = "Before nested table"
    nested = cell.add_table(rows=2, cols=2)
    nested.cell(0, 0).text = "Nested answer"
    nested.cell(0, 1).text = "2 marks"
    deeper = nested.cell(1, 0).add_table(rows=1, cols=2)
    deeper.cell(0, 0).text = "Deep answer"
    deeper.cell(0, 1).text = "3 marks"
    nested.cell(1, 1).text = "Deep explanation"
    cell.add_paragraph("After nested table")
    outer.cell(0, 1).text = "Outer explanation"
    document.add_paragraph("Memo after the outer table")
    stream = BytesIO()
    document.save(stream)
    assert memo_text(stream.getvalue(), "nested.docx") == (
        "Memo before the outer table\nBefore nested table\n"
        "Nested answer | 2 marks\nDeep answer | 3 marks\nDeep explanation\n"
        "After nested table | Outer explanation\nMemo after the outer table"
    )


def test_image_only_pdf_is_refused_as_a_scan() -> None:
    with pytest.raises(ValueError) as error:
        memo_text(pdf_bytes(""), "scan.pdf")
    assert str(error.value) == (
        "scan.pdf has no text to read (it is a scan); upload the typed memo, or the TOML"
    )


@pytest.mark.parametrize("suffix", ["pdf", "docx"])
@pytest.mark.parametrize("count", [0, 19, 20])
def test_minimum_text_counts_non_whitespace_characters(suffix: str, count: int) -> None:
    # ReportLab's built-in font renders tabs as visible squares, not whitespace.
    separator = "   " if suffix == "pdf" else " \t"
    text = separator.join("x" * count)
    data = pdf_bytes(text) if suffix == "pdf" else docx_bytes(text)
    if count < 20:
        with pytest.raises(ValueError, match="has no text to read"):
            memo_text(data, f"short.{suffix}")
    else:
        assert len("".join(memo_text(data, f"short.{suffix}").split())) == 20


@pytest.mark.parametrize("filename", ["old.doc", "notes.txt", "old.DOC", "memo"])
def test_unsupported_suffix_names_file_and_explains_accepted_types(filename: str) -> None:
    with pytest.raises(ValueError) as error:
        memo_text(b"not parsed", filename)
    message = str(error.value)
    assert filename in message
    assert ".pdf" in message and ".docx" in message
    assert ".doc must be saved as .docx" in message


@pytest.mark.parametrize("password", ["secret", ""])
def test_encrypted_pdf_is_refused_even_with_an_empty_password(password: str) -> None:
    writer = PdfWriter()
    writer.append(PdfReader(BytesIO(pdf_bytes("A typed memo with enough text to read."))))
    writer.encrypt(password)
    stream = BytesIO()
    writer.write(stream)
    with pytest.raises(ValueError, match=r"locked\.pdf.*encrypted"):
        memo_text(stream.getvalue(), "locked.pdf")


@pytest.mark.parametrize("suffix", ["pdf", "docx"])
def test_oversize_is_refused_before_parsing(suffix: str) -> None:
    assert MAX_MEMO_BYTES == 10 * 1024 * 1024
    filename = f"large.{suffix}"
    with pytest.raises(ValueError) as error:
        memo_text(b"x" * (MAX_MEMO_BYTES + 1), filename)
    message = str(error.value)
    assert filename in message and "10" in message
    assert "cannot open" not in message.lower()


@pytest.mark.parametrize("suffix", ["pdf", "docx"])
@pytest.mark.parametrize("data", [b"", b"garbage bytes"])
def test_unreadable_file_has_teacher_facing_error(suffix: str, data: bytes) -> None:
    filename = f"broken.{suffix}"
    with pytest.raises(ValueError) as error:
        memo_text(data, filename)
    assert "cannot open" in str(error.value).lower()
    assert filename in str(error.value)
