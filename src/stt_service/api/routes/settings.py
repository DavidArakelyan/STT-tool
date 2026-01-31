from fastapi import APIRouter, HTTPException, Body, Depends
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import os
from pathlib import Path

from stt_service.api.dependencies import APIKey

router = APIRouter(prefix="/settings", tags=["Settings"])

ENV_FILE_PATH = Path(".env")

class ConfigItem(BaseModel):
    key: str
    value: str
    comment: Optional[str] = None

class ConfigSection(BaseModel):
    name: str
    items: List[ConfigItem]

class AppConfig(BaseModel):
    sections: List[ConfigSection]

def parse_env_file() -> List[ConfigSection]:
    """Parse .env file into structured sections based on comments."""
    if not ENV_FILE_PATH.exists():
        # Fallback if no .env exists
        return []

    sections: List[ConfigSection] = []
    current_section = ConfigSection(name="General", items=[])
    sections.append(current_section)

    with open(ENV_FILE_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()

    for line in lines:
        line = line.strip()
        
        # Section Header
        if line.startswith("# ===") or line.startswith("#==="):
            # Extract section name "Application Settings" from "# === Application Settings ==="
            clean_name = line.replace("#", "").replace("=", "").strip()
            if clean_name:
                current_section = ConfigSection(name=clean_name, items=[])
                sections.append(current_section)
            continue
            
        # Comment or Empty
        if not line or line.startswith("#"):
            continue
            
        # Key-Value Pair
        if "=" in line:
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            
            # Handle inline comments
            comment = None
            if " #" in value:
                value, comment = value.split(" #", 1)
                value = value.strip()
                comment = comment.strip()
            elif "\t#" in value:
                value, comment = value.split("\t#", 1)
                value = value.strip()
                comment = comment.strip()
                
            current_section.items.append(ConfigItem(key=key, value=value, comment=comment))

    # Filter empty general section if unused
    if not sections[0].items and sections[0].name == "General" and len(sections) > 1:
        sections.pop(0)

    return sections

def write_env_file(config: AppConfig):
    """Write structured config back to .env file."""
    lines = []
    lines.append("# STT Service Configuration")
    lines.append("# Managed by Settings Editor")
    lines.append("")

    for section in config.sections:
        # Section Header
        lines.append(f"# {'=' * 75}")
        lines.append(f"# {section.name}")
        lines.append(f"# {'=' * 75}")
        
        for item in section.items:
            # Value formatting
            line = f"{item.key}={item.value}"
            if item.comment:
                line += f"  # {item.comment}"
            lines.append(line)
        
        lines.append("") # Empty line after section

    with open(ENV_FILE_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

@router.get("", response_model=AppConfig)
async def get_settings(_api_key: APIKey):
    """Get current configuration from .env file."""
    try:
        sections = parse_env_file()
        return AppConfig(sections=sections)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("", response_model=Dict[str, str])
async def update_settings(config: AppConfig, _api_key: APIKey):
    """Update .env configuration."""
    try:
        write_env_file(config)
        return {"message": "Configuration saved. You may need to restart functionality that relies on static variables."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
