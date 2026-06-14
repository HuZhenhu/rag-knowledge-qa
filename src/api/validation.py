"""输入验证 — 包含 Prompt 注入防护 + 文件预检查 + 内容防泄露"""
import re
from fastapi import HTTPException


# === Prompt 注入检测模式 ===
INJECTION_PATTERNS = [
    # 英文注入
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"override\s+(your\s+)?instructions",
    r"disregard\s+(all\s+)?prior",
    r"forget\s+(all\s+)?previous",
    r"you\s+are\s+now\s+(a\s+)?(unrestricted|free|unlimited)",
    r"act\s+as\s+if\s+you\s+have\s+no\s+restrictions",
    r"pretend\s+you\s+(are|have)\s+(no\s+)?restrictions",
    r"system\s+prompt\s*:",
    r"reveal\s+(your\s+)?(system\s+)?prompt",
    r"show\s+(me\s+)?(your\s+)?(system\s+)?prompt",
    r"output\s+(your\s+)?(system\s+)?prompt",
    # 中文注入
    r"忽略(之前|上面|以上|前面|先前)的(所有|全部)?(指令|规则|限制|约束)",
    r"忘掉(之前|上面|以上|前面|先前)的(所有|全部)?(指令|规则|限制|约束)",
    r"你现在(是|变成|作为)(一个)?(不受限制|没有限制|自由|无约束)",
    r"假装你(没有|不受)(任何)?(限制|约束|规则)",
    r"执行(以下)?指令",
    r"(告诉|输出|显示|泄露|透露)(我|出来)?(你的)?(系统提示|提示词|指令|规则)",
    r"(系统|内置)提示词(是|的内容)",
    r"把(系统|内置)提示(词)?(告诉|输出|显示|泄露)",
    r"(解除|绕过|突破)(你的)?(限制|约束|规则)",
    r"(越过|绕过)(所有|全部)?(安全|防护|限制)",
    # 通用危险模式
    r"you\s+are\s+DAN",
    r"do\s+anything\s+now",
    r"jailbreak",
]

# === 内容防泄露检测模式 ===
LEAK_PATTERNS = [
    # 中文：批量导出查询
    r"把(所有|全部|全部的)(chunk|文档片段|检索结果|知识库内容|数据|向量)(列出来|显示|输出|导出|打印)",
    r"(列出|显示|输出|导出|打印)(所有|全部)(的)?(chunk|文档片段|检索结果|内容|数据|向量)",
    r"导出(所有|全部)(的)?(知识库|文档|数据|内容)",
    r"(知识库|文档|数据|内容)(全部|所有)(输出|导出|显示|列出来)",
    r"列出所有(的)?(embedding|向量|embeddings?)",
    # 英文：批量导出查询
    r"dump\s+(all\s+)?(chunks?|documents?|data|vectors?|results?)",
    r"show\s+(me\s+)?(all\s+)?(chunks?|documents?|data|vectors?|results?)",
    r"export\s+(all\s+)?(data|content|documents?|chunks?)",
    r"list\s+(all\s+)?(chunks?|documents?|data|vectors?|results?)",
    r"print\s+(all\s+)?(chunks?|documents?|data|vectors?)",
    r"display\s+(all\s+)?(chunks?|documents?|data|vectors?)",
]

# === 文件类型魔数签名 ===
FILE_SIGNATURES = {
    # PDF: %PDF
    ".pdf": b"%PDF",
    # DOCX/ZIP: PK (ZIP magic bytes)
    ".docx": b"PK",
    ".xlsx": b"PK",
    # 图片: PNG (‰PNG)、JPEG (ÿØÿ)
    ".png": b"\x89PNG",
    ".jpg": b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff",
}

# 文本类文件：检查是否包含大量非文本字节
TEXT_EXTENSIONS = {".md", ".txt", ".log"}

MAX_QUERY_LENGTH = 2000
MAX_SESSION_ID_LENGTH = 100


def validate_query(query: str) -> str:
    """验证并清洗用户查询，包含 Prompt 注入检测 + 内容防泄露"""
    if not query or not query.strip():
        raise HTTPException(status_code=400, detail="查询不能为空")

    query = query.strip()

    if len(query) > MAX_QUERY_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"查询长度不能超过{MAX_QUERY_LENGTH}字符"
        )

    # Prompt 注入检测
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, query, re.IGNORECASE):
            raise HTTPException(
                status_code=400,
                detail="查询包含不允许的内容"
            )

    # 内容防泄露检测
    for pattern in LEAK_PATTERNS:
        if re.search(pattern, query, re.IGNORECASE):
            raise HTTPException(
                status_code=400,
                detail="查询包含不允许的内容"
            )

    return query


def validate_session_id(session_id: str | None) -> str | None:
    """验证会话ID"""
    if session_id is None:
        return None

    session_id = session_id.strip()

    if len(session_id) > MAX_SESSION_ID_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"会话ID长度不能超过{MAX_SESSION_ID_LENGTH}字符"
        )

    if not re.match(r'^[a-zA-Z0-9\-_]+$', session_id):
        raise HTTPException(status_code=400, detail="会话ID格式无效")

    return session_id


def validate_file_content(content: bytes, extension: str) -> bool:
    """验证文件内容是否与扩展名匹配
    
    Args:
        content: 文件内容（字节）
        extension: 文件扩展名（如 .md, .pdf）
    
    Returns:
        True 如果验证通过
    
    Raises:
        HTTPException: 如果文件内容与扩展名不匹配
    """
    if not content:
        raise HTTPException(status_code=400, detail="文件内容为空")

    extension = extension.lower()

    # 未知扩展名，跳过检查
    if extension not in FILE_SIGNATURES and extension not in TEXT_EXTENSIONS:
        return True

    # 检查魔数签名
    if extension in FILE_SIGNATURES:
        signature = FILE_SIGNATURES[extension]
        if not content.startswith(signature):
            raise HTTPException(
                status_code=400,
                detail=f"文件内容与扩展名不匹配：预期 {extension} 格式，但文件头不符合"
            )
        return True

    # 文本类文件：检查是否包含大量非文本字节
    if extension in TEXT_EXTENSIONS:
        # 读取前 1024 字节检查
        sample = content[:1024]
        # 计算非 ASCII 字符比例（排除常见的 UTF-8 多字节字符）
        non_text_bytes = sum(
            1 for b in sample
            if b < 0x09 or (0x0E <= b < 0x20 and b != 0x1B) or b == 0x7F
        )
        # 如果非文本字符超过 10%，可能是伪装的二进制文件
        if len(sample) > 0 and non_text_bytes / len(sample) > 0.1:
            raise HTTPException(
                status_code=400,
                detail=f"文件内容与扩展名不匹配：{extension} 文件包含大量非文本字符"
            )
        return True

    return True


def sanitize_output(text: str) -> str:
    """清洗输出内容，防止敏感信息泄露"""
    if not text:
        return text

    # 移除可能泄露的系统提示相关内容
    sensitive_patterns = [
        r'根据系统提示[^。]*',
        r'系统提示词[^。]*',
        r'我的指令是[^。]*',
        r'我的规则是[^。]*',
        r'我的设定是[^。]*',
        r'system prompt[^。]*',
        r'my instructions are[^。]*',
    ]

    result = text
    for pattern in sensitive_patterns:
        result = re.sub(pattern, '', result, flags=re.IGNORECASE)

    return result.strip()
