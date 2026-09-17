import chromadb                                                             # ChromaDB: 벡터 데이터베이스 라이브러리
import pandas as pd                                                         # Pandas: 데이터 분석 및 조작 라이브러리
from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction      # ChromaDB에서 OpenAI 임베딩 모델을 사용하기 위한 함수
from dotenv import load_dotenv                                              # dotenv: .env 파일에서 환경 변수(API Key 등)를 로드하는 라이브러리
from openai import OpenAI

load_dotenv()                                                               # .env 파일에 등록된 환경 변수 불러오기
openai_client = OpenAI()                                                    # open ai 클라이언트

debug_check = False                                                         # 기본은 debug 출력 False

# 1. DB 경로(db_path)를 전달받아 PersistentClient 객체를 생성하는 함수
def get_chroma_client(db_path):
    return chromadb.PersistentClient(path=db_path)                          # 지정된 디스크 경로에 데이터를 저장하는 클라이언트 반환

# 2. DB 경로(db_path)를 전달받아 PersistentClient 객체를 생성하는 함수
def set_collection(client, collection_name, ids, docs, metadata):
    collection = get_collection(client, collection_name)                    # 1. 전달받은 collection_name으로 컬렉션 객체를 가져옴 (없으면 자동 생성)
    collection.upsert(ids=ids, documents=docs, metadatas=metadata)          # ID가 없으면 추가, 같은 ID가 있으면 중복 생성 없이 기존 데이터 갱신


# 3. 클라이언트와 컬렉션 이름(collection_name)을 받아 컬렉션을 가져오거나 새로 생성하는 함수
def get_collection(client, collection_name):
    embedding_fn = OpenAIEmbeddingFunction(model_name="text-embedding-3-small")     # 사용할 OpenAI 임베딩 모델 지정
    return client.get_or_create_collection(                                         # 컬렉션 객체가 없으면 생성
        name=collection_name,                                                       # 컬렉션 이름 지정
        embedding_function=embedding_fn,                                            # 데이터 자동 벡터 변환에 사용할 임베딩 함수 주입
        configuration={'hnsw': {"space": "cosine"}}                                 # 벡터 유사도 측정 지표로 코사인 유사도(Cosine Similarity) 지정
    )

# 4. 지정한 컬렉션(collection_name)을 안전하게 삭제하는 함수
def delete_collection(client, collection_name):
    client.delete_collection(name=collection_name)                                  # 해당 컬렉션 삭제 실행
    print(f"[{collection_name}] 컬렉션이 삭제되었습니다.")                             # 삭제 성공 시 메시지 출력

def get_load_collection(db_path, collection_name):
    embedding_fn = OpenAIEmbeddingFunction(model_name="text-embedding-3-small")
    load_client = chromadb.PersistentClient(path=db_path)
    
    # return 문 바로 뒤에 '=' 할당문 없이 메서드 호출 결과를 직접 반환
    return load_client.get_collection(
        name=collection_name,
        embedding_function=embedding_fn
    )

def format_context(query_results):
    metadatas = query_results["metadatas"][0]
    documents = query_results["documents"][0]

    context_text = ""
    for i, (meta, doc) in enumerate(zip(metadatas, documents), start=1):
        context_text += f"{i}. 대표메뉴: {meta.get('대표메뉴')}\n"
        context_text += f"     검색확인명: {meta.get('검색확인명')}\n"
        context_text += f"     음식종류: {meta.get('음식종류')}\n"
        context_text += f"     주소_원본: {meta.get('주소_원본')}\n"
        context_text += f"     가게명_원본: {meta.get('가게명_원본')}\n"
        context_text += f"   - 특징/태그: {doc}\n\n"

    return  context_text       


# ==========================================
# 실행부 (호출 시 라인에서 직접 값 명시)
# ==========================================

# DB 경로를 직접 넘겨서 클라이언트 생성
client = get_chroma_client(db_path="restaurant_list_chroma_db")

# 삭제하고 싶을 때 직접 컬렉션 이름 지정해서 호출
delete_collection(client=client, collection_name="restaurant_collection")

# 데이터 없으면 등록 있으면 가져오기
df = pd.read_csv("./restaurant_list_final_vector.csv").reset_index(drop=True)
docs = df['오늘뭐먹지_검색태그'].tolist()
ids = df.index.astype(str).tolist()
metadata = df[['가게명_원본','주소_원본','검색확인명','음식종류','대표메뉴']].to_dict('records')
set_collection(client=client, collection_name="restaurant_collection",ids=ids,docs=docs,metadata=metadata)

load_collection = get_load_collection(db_path="restaurant_list_chroma_db",collection_name="restaurant_collection")


question = input("오늘 점심 뭐 먹고 싶나요?") # '짜장면 먹고 싶을때'

# RAG 원본 디버그 설정시 출력 
if(debug_check):
    print("==========================================================================")
    print( f" RAG 원본: {load_collection.query(query_texts=[question], n_results=4)} " ) 
    print("==========================================================================")

 
context = format_context(load_collection.query(query_texts=[question], n_results=5))

# RAG 원본 정리 디버그 설정시 출력 
if(debug_check):
    print("==========================================================================")
    print( f" RAG 원본 정리: {context}" ) 
    print("==========================================================================")

prompt = f"""
아래 [근거 자료]만 참고해서 질문에 답하세요
자료에 없으면 '자료에 없음'이라고 답하세요
설명을 자세히 해주세요.

[근거자료]
{context}

[질문]
{question}
"""

response_rag = openai_client.chat.completions.create(
    model="gpt-5.6-luna",
    messages=[
        {'role':'user','content':prompt}

    ]
)

print("추천 은 다음과 같습니다.")
print("==========================================================================")
print(response_rag.choices[0].message.content)
print("==========================================================================")


