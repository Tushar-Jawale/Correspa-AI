"""Build Chroma index from data/*.txt. Safe to run on Render (no blocking RAG test)."""
from pathlib import Path

from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_community.document_loaders import TextLoader
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter


def build_index(persist_directory: str = "db") -> None:
    load_dotenv()
    root = Path(__file__).resolve().parent
    data_dir = root / "data"
    docs = []
    for path in sorted(data_dir.glob("*.txt")):
        docs.extend(TextLoader(str(path), encoding="utf-8").load())
    if not docs:
        raise FileNotFoundError(f"No .txt files found in {data_dir}")

    print("Loading & chunking docs...", flush=True)
    splitter = RecursiveCharacterTextSplitter(chunk_size=300, chunk_overlap=50)
    chunks = splitter.split_documents(docs)

    print("Creating vector embeddings...", flush=True)
    embeddings = GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-001")
    Chroma.from_documents(chunks, embeddings, persist_directory=str(root / persist_directory))
    print("Knowledge index ready.", flush=True)


if __name__ == "__main__":
    build_index()
