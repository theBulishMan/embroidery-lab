const $=s=>document.querySelector(s);
let action='login';
const destinations={admin:'/admin',account:'/account'};
const destination=destinations[new URLSearchParams(location.search).get('next')]||'/';
function showError(text){$('#form-error').textContent=text;$('#form-error').hidden=!text}
function mode(next){
  action=next;const registration=next==='register';showError('');
  for(const [id,value] of [['login-tab','login'],['register-tab','register']]){const active=next===value;$('#'+id).setAttribute('aria-selected',String(active));$('#'+id).tabIndex=active?0:-1}
  $('#form-title').textContent=registration?'从这里开始创作':'欢迎回来';
  $('#form-description').textContent=registration?'使用邀请码，创建你的个人工作台。':'登录，继续你的刺绣创作。';
  $('#register-fields').hidden=!registration;
  $('#confirm-password').required=registration;$('#invite-code').required=registration;
  $('#confirm-password').disabled=!registration;$('#invite-code').disabled=!registration;
  $('#password').autocomplete=registration?'new-password':'current-password';
  $('#password').placeholder=registration?'设置密码，至少 10 个字符':'输入你的密码';
  $('#submit-auth span').textContent=registration?'注册并进入工作台':'进入工作台';
  $('#form-footnote').textContent=registration?'已有账号？切换「账号登录」即可。':'你的作品，会在这里等你。';
}
$('#login-tab').onclick=()=>mode('login');$('#register-tab').onclick=()=>mode('register');
$('.auth-tabs').onkeydown=e=>{if(['ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();mode(action==='login'?'register':'login');$(action==='login'?'#login-tab':'#register-tab').focus()}};
document.querySelectorAll('[data-password]').forEach(button=>button.onclick=()=>{const input=$('#'+button.dataset.password),show=input.type==='password';input.type=show?'text':'password';button.setAttribute('aria-label',show?'隐藏密码':'显示密码');button.setAttribute('aria-pressed',String(show))});
$('#auth-form').onsubmit=async e=>{
  e.preventDefault();showError('');
  if(action==='register'&&$('#password').value!==$('#confirm-password').value){showError('两次输入的密码不一致，请检查。');$('#confirm-password').focus();return}
  const controls=[...document.querySelectorAll('button')];controls.forEach(b=>b.disabled=true);$('#submit-auth span').textContent=action==='register'?'正在创建工作台…':'正在登录…';
  try{
    const response=await fetch('/api/auth',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,username:$('#username').value,password:$('#password').value,invite_code:$('#invite-code').value})});
    const result=await response.json();if(!response.ok)throw new Error(result.error||'暂时无法登录，请稍后再试。');
    // Navigation must stay on this website; do not accept arbitrary return URLs.
    location.replace(action==='register'?'/':destination);
  }catch(error){showError(error instanceof TypeError?'网络连接未完成，请检查连接后重试。':error.message);controls.forEach(b=>b.disabled=false);$('#submit-auth span').textContent=action==='register'?'注册并进入工作台':'进入工作台'}
};
