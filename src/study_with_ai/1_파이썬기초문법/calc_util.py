# 1. 여러 지출 금액을 입력받아 최종 지출 금액 반환하는 함수 만들기
# 2. 예산과 현재 지출 금액을 받아 남은 예산을 반환하는 함수 만들기
# 3. 지출 금액과 할인율을 받아 할인된 금액을 반환하는 함수 만들기


# 1. 여러 지출 금액을 입력받아 최종 지출 금액 반환하는 함수 만들기
def get_expense_list(expense_item_list):

    expense_price = 0

    for expense_item in expense_item_list:
        expense_price += expense_item        
    return expense_price


# 2. 예산과 현재 지출 금액을 받아 남은 예산을 반환하는 함수 만들기
def get_remaining_budget(budget, expense_price):
    return (budget - expense_price)

# 3. 지출 금액과 할인율을 받아 할인된 금액을 반환하는 함수 만들기
def get_calc_discount_amount(expense_price, discount_rate):
    return int(expense_price - (expense_price * (discount_rate / 100 )))




