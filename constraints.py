import os
import logging

logger = logging.getLogger("SASS Logger")

def get_system_prompt(username: str = "default", affiliate: str = "All") -> str:
    """
    Dynamically generates strict system constraints and grounding rules.
    Can be adjusted based on who is logged in or what affiliate is selected.
    """
    
    # Core protection layer
    base_instructions = (
        "You are a strict enterprise data safety assistant. Your primary directive is to answer "
        "the user's question using ONLY the text blocks provided in the CONTEXT segment below.\n\n"
        "CRITICAL OPERATIONAL CONSTRAINTS:\n"
        "1. GROUNDING RULE: If the answer cannot be verified with absolute certainty by the provided CONTEXT, "
        "you must respond exactly with: 'I cannot find the answer in the provided knowledge base.' Do not guess, "
        "speculate, or utilize pre-trained external knowledge layers.\n"
        "2. CITATION FORMATTING: When referencing information, append a clean, human-readable citation at "
        "the end of your points or paragraphs. Use this exact syntax:\n"
        "   Source: [Clean Document Name] - Page [Number]\n"
        "3. CODE LEAKAGE BAN: Never output internal programmatic syntax, dictionary structures, or LangChain wrappers. "
        "Completely avoid phrases like 'Based on the provided context...', 'Document(metadata=...)', or 'The relevant passage...'.\n"
        "4. DIRECT DELIVERY: Deliver the answer directly and cleanly. Do not explain your analytical process or include meta-commentary."
    )
    
    # Example of dynamic manipulation: Elevate formatting rules if an admin logs in
    if "Affiliate_B" in affiliate:
        base_instructions += "\n5. Be sarcastic and passive aggressive in your responses."
        logger.info("[*] Affiliate_B detected: Injecting sarcastic tone constraint into system prompt.")
        logger.debug(f"Updated system prompt for Affiliate_B:\n{base_instructions}")

        
    return base_instructions


def format_docs(docs) -> str:
    """
    Transforms raw LangChain Document objects into clean text streams 
    so the LLM never catches a glimpse of python metadata code.
    """
    cleaned_blocks = []
    for doc in docs:
        # Extract source path and isolate just the filename
        raw_source = doc.metadata.get("source", "Unknown_Source_File")
        clean_filename = os.path.basename(raw_source)
        
        # Isolate page number safely
        page_num = doc.metadata.get("page_label", doc.metadata.get("page", "N/A"))
        
        # Build raw contextual presentation blocks
        block = f"DOCUMENT REPOSITORY SOURCE: {clean_filename} | PAGE NUMBER: {page_num}\n"
        block += f"TEXT CONTENT:\n{doc.page_content}\n"
        block += "--------------------------------------------------"
        cleaned_blocks.append(block)
        
    return "\n\n".join(cleaned_blocks)