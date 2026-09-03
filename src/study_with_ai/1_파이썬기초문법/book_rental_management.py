# 1. 책 메목 과 대여 가능 여부를 받아 대여 결과를 반환하는 함수
# book_name : 책이름
# is_borrow : 대여가능여부
def get_borrow_book(book_name,is_borrow):

    ret_str = ""

    if( is_borrow == True ):
        ret_str = f"{book_name} 대여가 완료되었습니다."
    elif( is_borrow == False ):             
        ret_str = f"{book_name} 대여가 불가능합니다."

    return ret_str


# 2. 대여 일수와 하루 연체료 를 받아 연체료를 계산하는 함수 만들기
# rental_duration : 대여 일수 
# daily_fine_rate : 하루 연체료
def get_overdue_free(rental_duration, daily_fine_rate):
    return  f" 연체료는  {rental_duration * daily_fine_rate} 원 입니다." 
     

# 3. 여러 권의 책 체목을 입력받아 대여한 책 목록을 반환하는 함수 만들기
def book_name_list(*book_name_list):

    for book_name in book_name_list.items():
        print(book_name)


    
    
