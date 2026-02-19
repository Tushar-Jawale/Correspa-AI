from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain_groq import ChatGroq
from langchain_chroma import Chroma
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from .structure_outputs import *
from .prompts import *

class Agents():
    def __init__(self):
        # Choose which LLMs to use for each agent (GPT-4o, Gemini, LLAMA3)
        llama = ChatGroq(model_name="openai/gpt-oss-120b", temperature=0.1)
        gemini = ChatGoogleGenerativeAI(model="gemini-3.8-flash", temperature=0.1)
        
        # QA assistant chat
        embeddings = GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-001")
        vectorstore = Chroma(persist_directory="db", embedding_function=embeddings)
        retriever = vectorstore.as_retriever(search_kwargs={"k": 3})

        # Categorize email chain
        email_category_prompt = PromptTemplate(
            template=CATEGORIZE_EMAIL_PROMPT, 
            input_variables=["email"]
        )
        # gpt-oss models need json_schema; default tool-calling breaks on Groq
        self.categorize_email = (
            email_category_prompt | 
            llama.with_structured_output(CategorizeEmailOutput, method="json_schema")
        )

        # Used to design queries for RAG retrieval
        generate_query_prompt = PromptTemplate(
            template=GENERATE_RAG_QUERIES_PROMPT, 
            input_variables=["email"]
        )
        self.design_rag_queries = (
            generate_query_prompt | 
            llama.with_structured_output(RAGQueriesOutput, method="json_schema")
        )
        
        # Generate answer to queries using RAG
        qa_prompt = ChatPromptTemplate.from_template(GENERATE_RAG_ANSWER_PROMPT)
        self.generate_rag_answer = (
            {"context": retriever, "question": RunnablePassthrough()}
            | qa_prompt
            | llama
            | StrOutputParser()
        )

        # Used to write a draft email based on category and related informations
        writer_prompt = ChatPromptTemplate.from_messages(
            [
                ("system", EMAIL_WRITER_PROMPT),
                MessagesPlaceholder("history"),
                ("human", "{email_information}")
            ]
        )
        self.email_writer = (
            writer_prompt | 
            llama.with_structured_output(WriterOutput, method="json_schema")
        )

        # Verify the generated emails
        proofreader_prompt = PromptTemplate(
            template=EMAIL_PROOFREADER_PROMPT, 
            input_variables=["initial_email", "generated_email"]
        )
        self.email_proofreader = (
            proofreader_prompt | 
            llama.with_structured_output(ProofReaderOutput, method="json_schema") 
        )
