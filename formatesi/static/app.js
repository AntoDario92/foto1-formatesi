'use strict';
// Local UI only: authentication and all student records are handled by the server.
document.querySelectorAll('[data-preview]').forEach(el=>{
 const name=el.dataset.preview, input=document.querySelector('[name="'+name+'"]'), fallback=el.textContent;
 if(input)input.addEventListener('input',()=>{el.textContent=input.value.trim()||fallback;});
});
const university=document.getElementById('ateneo');
if(university)university.addEventListener('change',()=>{
 const other=document.getElementById('other-ateneo');other.hidden=university.value!=='Altro ateneo';
 other.querySelector('input').required=!other.hidden;
});
if(university)university.dispatchEvent(new Event('change'));
document.querySelectorAll('form[data-upload]').forEach(form=>{
 let submitting=false;
 form.addEventListener('submit',async event=>{
  if(submitting){event.preventDefault();return;}
  event.preventDefault();
  const file=form.querySelector('input[type="file"]')?.files[0],button=form.querySelector('button[type="submit"],button:not([type])'),status=form.querySelector('[data-upload-status]');
  if(file && (file.size>5*1024*1024||!file.size)){status.textContent='Scegli un file non vuoto di massimo 5 MB.';return;}
  if(file&&!/\.(pdf|docx|txt)$/i.test(file.name)){status.textContent='Sono accettati PDF, DOCX e TXT.';return;}
  submitting=true;button.disabled=true;status.textContent='Invio in corso…';
  try{
   if(file){
    const base64=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});
    form.querySelector('[name="file_name"]').value=file.name;form.querySelector('[name="file_data"]').value=base64;
   }
   HTMLFormElement.prototype.submit.call(form);
  }catch(error){status.textContent='Non è stato possibile leggere il file. Riprova.';button.disabled=false;submitting=false;}
 });
});

if(!window.matchMedia('(prefers-reduced-motion: reduce)').matches){
 document.body.classList.add('reveal-ready');
 const reveal=new IntersectionObserver(entries=>{
  entries.forEach(entry=>{
   if(entry.isIntersecting){entry.target.classList.add('is-visible');reveal.unobserve(entry.target);}
  });
 },{threshold:.08,rootMargin:'0px 0px -40px'});
 document.querySelectorAll('.section,.university-section,.feature-section,.reviews-section').forEach(section=>reveal.observe(section));
}

document.querySelectorAll('form[data-wizard]').forEach(form=>{
 const steps=[...form.querySelectorAll('.wizard-step')],progress=[...form.querySelectorAll('.wizard-progress span')];
 if(!steps.length)return;
 let current=0;form.classList.add('wizard-ready');
 const show=index=>{
  current=Math.max(0,Math.min(index,steps.length-1));
  steps.forEach((step,i)=>{step.hidden=i!==current;});
  progress.forEach((item,i)=>item.classList.toggle('active',i<=current));
  steps[current].querySelector('input,select,textarea')?.focus({preventScroll:true});
 };
 form.addEventListener('click',event=>{
  const next=event.target.closest('.wizard-next'),back=event.target.closest('.wizard-back');
  if(next){
   const controls=[...steps[current].querySelectorAll('input,select,textarea')];
   const invalid=controls.find(control=>!control.checkValidity());
   if(invalid){invalid.reportValidity();return;}
   show(current+1);
  }
  if(back)show(current-1);
 });
 show(0);
});
