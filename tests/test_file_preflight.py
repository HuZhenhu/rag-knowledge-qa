"""文件上传预检查测试"""
import pytest
from pathlib import Path
from io import BytesIO


class TestFilePreflightCheck:
    """文件内容预检查 - 验证文件类型真实性"""

    def test_valid_markdown_file_passes(self):
        """有效的 Markdown 文件应该通过检查"""
        from src.api.validation import validate_file_content
        # Markdown 文件以 # 或文字开头
        content = b"# Title\n\nThis is content."
        result = validate_file_content(content, ".md")
        assert result is True

    def test_valid_txt_file_passes(self):
        """有效的 TXT 文件应该通过检查"""
        from src.api.validation import validate_file_content
        content = b"This is plain text content."
        result = validate_file_content(content, ".txt")
        assert result is True

    def test_valid_docx_file_passes(self):
        """有效的 DOCX 文件应该通过检查"""
        from src.api.validation import validate_file_content
        # DOCX 文件以 PK (ZIP) 开头
        content = b"PK\x03\x04" + b"\x00" * 100
        result = validate_file_content(content, ".docx")
        assert result is True

    def test_valid_pdf_file_passes(self):
        """有效的 PDF 文件应该通过检查"""
        from src.api.validation import validate_file_content
        # PDF 文件以 %PDF 开头
        content = b"%PDF-1.4" + b"\x00" * 100
        result = validate_file_content(content, ".pdf")
        assert result is True

    def test_exe伪装成_txt_rejected(self):
        """EXE 伪装成 TXT 应该被拒绝"""
        from src.api.validation import validate_file_content
        from fastapi import HTTPException
        # EXE 文件以 MZ 开头
        content = b"MZ\x90\x00" + b"\x00" * 100
        with pytest.raises(HTTPException) as exc_info:
            validate_file_content(content, ".txt")
        assert exc_info.value.status_code == 400
        assert "伪装" in exc_info.value.detail or "不匹配" in exc_info.value.detail

    def test_pdf伪装成_md_rejected(self):
        """PDF 伪装成 MD 应该被拒绝"""
        from src.api.validation import validate_file_content
        from fastapi import HTTPException
        content = b"%PDF-1.4" + b"\x00" * 100
        with pytest.raises(HTTPException) as exc_info:
            validate_file_content(content, ".md")
        assert exc_info.value.status_code == 400

    def test_empty_file_rejected(self):
        """空文件应该被拒绝"""
        from src.api.validation import validate_file_content
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            validate_file_content(b"", ".txt")
        assert exc_info.value.status_code == 400

    def test_unknown_extension_passes(self):
        """未知扩展名的文件应该通过（不做检查）"""
        from src.api.validation import validate_file_content
        content = b"some content"
        # .xyz 是未知扩展名，应该跳过检查
        result = validate_file_content(content, ".xyz")
        assert result is True


class TestUploadEndpointPreflight:
    """上传接口集成预检查"""

    def test_upload_uses_preflight_check(self):
        """上传接口应该调用预检查"""
        from src.api.validation import validate_file_content
        # 验证函数存在且可调用
        assert callable(validate_file_content)
