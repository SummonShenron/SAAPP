from langchain_ollama import OllamaLLM


llm = OllamaLLM(model="llama3", temperature=0.7, streaming=True)
