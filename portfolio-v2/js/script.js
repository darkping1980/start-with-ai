// 모바일 화면에서만 보이는 메뉴 버튼과 메뉴 목록을 가져옵니다.
const menuButton = document.querySelector('.menu-button');
const menu = document.querySelector('.menu');

// 버튼을 누를 때 메뉴를 열거나 닫고, 화면 읽기 도구에 현재 상태를 알려 줍니다.
menuButton?.addEventListener('click', () => {
  const isOpen = menu.classList.toggle('is-open');
  menuButton.setAttribute('aria-expanded', String(isOpen));
  menuButton.querySelector('.sr-only').textContent = isOpen ? '메뉴 닫기' : '메뉴 열기';
});

// 메뉴 항목을 누르면 메뉴를 닫아, 작은 화면에서 본문을 바로 볼 수 있게 합니다.
menu?.querySelectorAll('a').forEach((link) => {
  link.addEventListener('click', () => {
    menu.classList.remove('is-open');
    menuButton?.setAttribute('aria-expanded', 'false');
    if (menuButton) menuButton.querySelector('.sr-only').textContent = '메뉴 열기';
  });
});

// 매년 footer의 연도를 손으로 바꾸지 않아도 되도록 현재 연도를 넣습니다.
document.querySelector('#year').textContent = new Date().getFullYear();
