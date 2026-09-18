import pandas as pd
import json
import numpy as np
from sklearn.cluster import KMeans
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity


# ==============================================================================
# 프로그램 구조 정리 시작
# ==============================================================================

# 최초
# ====================================================
# 임베딩 모델 생성
# 파일 불러오기
# 대상 컬럼 벡터화 
# 신규 벡터 컬럼에 대상 컬럼 벡터 정보 설정
# 원본 벡터 확인
# 파일로 원래 컬럼 + 벡터 컬럼 저장
# ====================================================

# 최초 이후 두번째
# ====================================================
# 임베딩 모델 생성
# 벡터 컬럼 존재하는 파일 읽기
# 벡터 컬럼 문자열을 벡터화 처리
# 질문 입력 벡터화
# 질문 , 대상 벡터 코사인 유사도 비교 
# 상위 가게 목록 5개 출력
# ====================================================


# ==============================================================================
# 프로그램 구조 정리 끝
# ==============================================================================


# 모델 로그 설정 시작
model = SentenceTransformer(
    "dragonkue/BGE-m3-ko",
    device="cpu"
)   
# 모델 로그 설정 끝


# ==============================================================================
# 최초 파일 불러와서 벡터와 시작
# ==============================================================================

# df = pd.read_csv("../data/98_restaurant_list_enriched.csv")     # 파일 불러오기

# df['벡터_오늘뭐먹지_검색태그'] = df['오늘뭐먹지_검색태그']             # 벡터화 할 내용 필드 설정         
# # print(df.head()) # 임시출력 주석처리


# article_vec = model.encode(df['오늘뭐먹지_검색태그'].tolist(),normalize_embeddings=True)

# # 벡터를 DataFrame 컬럼에 저장 벡터 전체를 JSON 문자열로 저장
# df['벡터_오늘뭐먹지_검색태그'] = [ json.dumps(vector.tolist()) for vector in article_vec ]



# # 원본 + 벡터 확인
# for text, vector, saved_vector in zip(df['오늘뭐먹지_검색태그'], article_vec, df['벡터_오늘뭐먹지_검색태그']):
#     print("원본:", text)
#     print("벡터:", vector)
#     print("df 저장 벡터:", saved_vector)
#     print()


# # 벡터_오늘뭐먹지_검색태그 백터화 해서 저장
# df.to_csv("../data/98_restaurant_list_final_vector.csv",index=False,encoding="utf-8-sig")

# ==============================================================================
# 최초 파일 불러와서 벡터와 끝
# ==============================================================================

df = pd.read_csv("../data/98_restaurant_list_final_vector.csv")                 # 파일 불러오기
# print(df.head())

# CSV에 문자열로 저장된 벡터를
# json.loads()로 Python 리스트로 변환하고,
# 최종적으로 NumPy 2차원 배열로 변환
article_vec = np.array( 
    df['벡터_오늘뭐먹지_검색태그']
    .apply(json.loads)
    .tolist()
)

text_query_content = input("오늘 뭐 먹고 싶은지 입력하세요: ")                      # 질문 입력받기  
print(f"질문 :{text_query_content}")                                            # 질문 출력

text_query = model.encode([text_query_content],normalize_embeddings=True)       # 질문 벡터화
# print(text_query)  # 벡터값 임시 출력

api_sim = cosine_similarity(text_query , article_vec)                           # 질문 벡터와 전체 음식점 벡터의 코사인 유사도 계산

# 첫 번째 질문의 유사도 점수들을 가져와 오름차순 정렬 후, 역순으로 뒤집어 높은 유사도 순서의 인덱스를 구함
api_rank = api_sim[0].argsort()[::-1]

# 유사도가 높은 상위 5개 음식점의 정보를 순서대로 출력
for index in api_rank[:5]:
    print("가게명:", df.iloc[index]['가게명_원본'])             # 해당 인덱스의 가게명 출력
    print("검색태그:", df.iloc[index]['오늘뭐먹지_검색태그'])    # 해당 가게의 검색 태그 출력   
    print("유사도:", api_sim[0][index])                       # 질문과 해당 가게의 코사인 유사도 점수 출력
    print()                                                  # 결과 구분을 위한 빈 줄   

