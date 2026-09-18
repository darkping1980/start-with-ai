from dotenv import load_dotenv                                      # .env 환경 변수 로드
from langchain_chroma import Chroma                                 # Chroma 벡터 DB
from langchain_openai import OpenAIEmbeddings                       # OpenAI 임베딩
from langchain_core.documents import Document                       # LangChain 문서 객체
from pypdf import PdfReader                                         # PDF 파일 읽기
from langchain.chat_models import init_chat_model                   # Chat 모델 생성
from langchain_core.messages import SystemMessage, HumanMessage     # 시스템/사용자 메시지
from langchain_core.output_parsers import StrOutputParser           # LLM 결과를 문자열로 변환
from langchain_core.runnables import RunnablePassthrough            # 입력값을 그대로 다음 단계로 전달
from langchain_core.prompts import ChatPromptTemplate               # Chat 모델에 전달할 프롬프트 형식을 만들기 위한 클래스 SystemMessage, HumanMessage 등을 템플릿 형태로 구성할 때 사용
from operator import itemgetter

load_dotenv()                                                       # API 키 로드  

model = init_chat_model("openai:gpt-5.6-luna")                      # 모델 설정

debug_flag = False
DB_PATH = "../data/practice_k_ladder_2026"                          # 임베딩 및 저장    

SYSTME_PROMPT = """
너는 공공 정책 안내 도우미다.
아래 자료를 참고해서 답해라.
참고 자료에 없으면 "자료에 없음" 이라고 말해라
정확한 자격, 금액, 기한은 공고 확인이 필요하다고 꼭 덧붙여라
답 끝에 참고한 페이지 번호를 [p.60] 형식으로 적어라
"""


# pdf 파일 읽기
def load_pdf_reader():
    reader = PdfReader("../data/16-1_K희망사다리2026_모두의정책.pdf")
    total_k_ladder = len(reader.pages)
    if(debug_flag):
        print("======================================================")
        print( f" 희망 사다리 모두의 정책 페이지수 : {total_k_ladder}" )
        print("======================================================")

    return reader

# pdf_docs 배열에 저장해서 리턴
def set_pdf_docs(param_reader):
    pdf_docs = []       # pdf 리스트       
    # 페이지 별로 담기
    for i,  page in enumerate(param_reader.pages):
        text = page.extract_text()
        pdf_docs.append(Document(page_content=text,
                                metadata={"source":"K희망사다리2026_모두의정책",
                                        "page": i + 1 }))
    if(debug_flag):
        print("======================================================")
        print(pdf_docs[:10])    # 10 페이지 까지 출력        
        print("======================================================")

    return pdf_docs         

# 벡터 db 에 저장
def insert_vector_db(param_dbpath, param_pdf_docs):
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")           # 임베딩 모델 설정
    # 벡터스토어에 저장
    vectorstore = Chroma.from_documents(
        documents=param_pdf_docs,
        embedding=embeddings,
        collection_name="practice_k_ladder_2026",
        persist_directory=param_dbpath
    )

    reg_docs =  vectorstore.similarity_search("청년 지월 월세는 어떻게 신청하나요?",k=10)

    if(debug_flag):
        print("======================================================")
        print("응답내역 등록 여부 테스트")    # 10 페이지 까지 출력        
        print(reg_docs)    # 10 페이지 까지 출력        
        print("======================================================")

def conn_vector_db(param_dbpath):
    # 임베딩 모델 설정
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")

    load_vs = Chroma(
        collection_name="practice_k_ladder_2026",
        embedding_function=embeddings,
        persist_directory=param_dbpath
    )

    # 실제로 검색
    if(debug_flag):
        retreiver = load_vs.as_retriever(
            search_kwargs = {"k":2}
        )
        result = retreiver.invoke("신혼 부부 지원 정책")

        print("등록후 검색 테스트")
        for i, doc in enumerate(result, start=1):
            print("=" * 80)
            print(f"[검색 결과 {i}]")
            print(f"페이지 : {doc.metadata['page']}")
            print(f"출처   : {doc.metadata['source']}")
            print("-" * 80)
            print(doc.page_content)
            print()

    return load_vs

# 검색기 가져오기
def get_retreiver_mmr(param_load_vs):
    retreiver_mmr = param_load_vs.as_retriever(search_type="mmr", 
                                               search_kwargs={"k": 5, 
                                                "fetch_k": 50,
                                                "lambda_mult":0.25})

    # 디버그용 검색 테스트
    if debug_flag:
        result = retreiver_mmr.invoke("신혼 부부 지원 정책")

        print("=" * 80)
        print("MMR Retriever 검색 테스트")
        print("=" * 80)

        for i, doc in enumerate(result, start=1):
            print(f"[검색 결과 {i}]")
            print(f"페이지 : {doc.metadata['page']}")
            print(f"출처   : {doc.metadata['source']}")
            print("-" * 80)
            print(doc.page_content)
            print("=" * 80)

    return retreiver_mmr


# 검색 결과 문서를 받았을때 메타데이터와 내용을 합쳐서 text 로 변환하는 함수 작성
def format_docs(docs):
    context = ""

    for doc in docs:
        context += f" [p.{doc.metadata["page"]}] \n {doc.page_content} \n\n"

    return context      


# reader = load_pdf_reader()                                          # pdf 불러오기
# pdf_docs = set_pdf_docs(reader)                                     # pdf docs 형태의 배열에 담기 
# insert_vector_db(param_dbpath=DB_PATH, param_pdf_docs=pdf_docs)     # 벡터 db 에 저장

load_vs = conn_vector_db(param_dbpath=DB_PATH)                        # 벡터 DB 접속  
retreiver_mmr = get_retreiver_mmr(load_vs)                            # 검색기 가져오기  


# rag_prompt 
# ============================================================
# 1. RAG에서 사용할 프롬프트 템플릿 생성
# ============================================================

rag_prompt = ChatPromptTemplate.from_messages([
    # system: AI에게 "어떻게 답변해야 하는지" 역할/규칙을 전달  SYSTEM_PROMPT 변수에 미리 작성해 둔 내용을 사용
    ('system', SYSTME_PROMPT),  
    # human: 실제 사용자 질문을 만들기 위한 템플릿 {context} → 벡터 DB에서 검색한 관련 문서가 들어갈 자리 {question} → 사용자가 입력한 실제 질문이 들어갈 자리
    ('human', '참고자료\n{context}\n\n질문\n{question}') 
])


# ============================================================
# 2. RAG Chain 구성
# ============================================================

# # 질문 → 문서 검색 → 프롬프트 생성 → LLM 답변 → 문자열 반환
# rag_chain = {
#     "context": (retreiver_mmr | format_docs),   # 관련 문서 검색
#     "question": RunnablePassthrough()            # 질문 그대로 전달
# } | rag_prompt | model | StrOutputParser()

# # RAG 실행
# result = rag_chain.invoke("청년 월세 지원 정책 찾아줘")

# 입력의 question 값을 검색에도 사용하고, 프롬프트 question에도 사용
rag_chain = {
    "context": itemgetter("question") | retreiver_mmr | format_docs,
    "question": itemgetter("question")
} | rag_prompt | model | StrOutputParser()

# question이라는 이름으로 전달
result = rag_chain.invoke({
    "question": "청년 월세 지원 정책 찾아줘"
})



# ============================================================
# 4. 최종 결과 출력
# ============================================================

print("===============================")
print("검색결과")
print("===============================")

# StrOutputParser()를 거쳤기 때문에
# result는 최종적으로 문자열(str) 형태
print(result)






