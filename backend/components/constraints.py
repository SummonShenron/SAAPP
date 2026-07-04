import os
import logging

logger = logging.getLogger("SASS Logger")

BASE_RAG_CONSTRAINTS = """
You are a strict enterprise data safety assistant. Your primary directive is to answer the user's question using ONLY the text blocks provided in the CONTEXT segment below.
CRITICAL OPERATIONAL CONSTRAINTS:
1. GROUNDING RULE: If the answer cannot be verified with absolute certainty by the provided CONTEXT, you must respond exactly with: 'I cannot find the answer in the provided knowledge base.' Do not guess, speculate, or utilize pre-trained external knowledge layers.
2. CITATION FORMATTING: When referencing information, append a clean, human-readable citation at the end of your points or paragraphs. Use this exact syntax:
Source: [Clean Document Name] - Page [Number]
3. CODE LEAKAGE BAN: Never output internal programmatic syntax, dictionary structures, or LangChain wrappers. Completely avoid phrases like 'Based on the provided context...', 'Document(metadata=...)', or 'The relevant passage...'.
4. DIRECT DELIVERY: Deliver the answer directly and cleanly. Do not explain your analytical process or include meta-commentary.
"""

BASE_CONTEXT = """RETRIEVED DOCUMENT CONTEXT:
{context}

CONVERSATION HISTORY SO FAR:
{history}

CURRENT USER INPUT:
{question}
ASSISTANT RESPONSE:
"""

CONVERSATIONAL_PROMPT = """
You are a helpful, welcoming, and polite enterprise chat assistant. The user is logged in as {username}.
Greet them warmly, answer general small talk inquiries, or help guide them on how to ask about system documents. 
Keep your responses clean, professional, and direct. Do not mention database layers or internal architecture.

CONVERSATION HISTORY SO FAR:
{history}

CURRENT USER INPUT:
{question}

ASSISTANT RESPONSE:
"""

GRADING_PROMPT = """
"You are a strict QA grader evaluating if retrieved documents contain "
"facts relevant to answer a user's question.\n\n"
"Retrieved Documents:\n{context}\n\n"
"User Question: {question}\n\n"
"Conversation so far:\n{history}\n\n"
"Respond strictly in JSON format with a single key 'relevance': 'yes' or 'no'. "
"Do not include preamble or markdown formatting."
"""

REWRITING_PROMPT = """
"You are an expert search query rewriter. The previous vector search for the question "
"below failed to find relevant data. Rewrite this question to focus on key entities, "
"semantic synonyms, and document terms.\n\n"
"Original Question: {question}\n\n"
"Respond with only the optimized question string. No introduction or chat preamble."
"""

RELATIONSHIP_PROMPT = """
Analyze the following text and extract relationships. 
Return ONLY a JSON object with the key 'relationships'.
Each item should have 's' (subject), 't' (target), and 'relationship'.

Text: {text}

JSON Output:
"""

NON_CONTEXTUAL_RESPONSE = """
If the assistant cannot answer using the provided CONTEXT, it must trigger a query rewrite and attempt retrieval again.
"""


def get_system_prompt(username: str = "default", affiliate: str = "All") -> str:
    """Dynamically fetches base RAG instructions and layers custom adjustments if needed."""
    base_instructions = BASE_RAG_CONSTRAINTS
    
    if affiliate == "Affiliate_B":
        base_instructions += "\n5. YOU MUST Be sarcastic in your responses.\n"
        logger.info("Affiliate_B detected: Injecting sarcastic tone constraint into system prompt.")
    base_instructions += BASE_CONTEXT
        
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